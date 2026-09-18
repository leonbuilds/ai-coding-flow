#!/usr/bin/env python3
"""wb：个人 AI 工作台。本仓库是唯一真相源，同步到 Claude Code 与 Codex，并检查漂移。

  wb sync [--host claude|codex|all] [--no-rules] [--dry-run]
  wb doctor
  wb diff
  wb mcp import | diff | apply [--host ...] [--yes]
  wb uninstall [--host ...]

只依赖 Python 3.9+ 标准库。所有写操作只动 wb 自己装的东西：
  - skills / agents：软链接，同名的已有文件一律跳过并报冲突
  - 全局规则：CLAUDE.md / AGENTS.md 里 <!-- ai-coding-flow:begin --> … end 之间的区块，改前备份
  - MCP：默认只显示差异，apply 时逐条确认；从不删除清单之外的 server
路径可用 CLAUDE_HOME / CODEX_HOME / AI_FLOW_HOME 覆盖（测试用）。
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
HOME = Path.home()
CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", HOME / ".claude"))
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
FLOW_HOME = Path(os.environ.get("AI_FLOW_HOME", HOME / ".ai-flow"))
CLAUDE_JSON = Path(os.environ.get("CLAUDE_JSON", HOME / ".claude.json"))
MCP_MANIFEST = Path(os.environ.get("WB_MCP_MANIFEST", KIT / "workbench" / "mcp.json"))
HOSTS = ("claude", "codex")
RULES_FILE = {"claude": CLAUDE_HOME / "CLAUDE.md", "codex": CODEX_HOME / "AGENTS.md"}
BEGIN_RE = re.compile(r"<!-- ai-coding-flow:begin sha=([0-9a-f]{8}) -->\n(.*?)<!-- ai-coding-flow:end -->\n?", re.S)
FROM_HOST = "<from-host>"  # mcp.json 里的敏感 env 值不入库，apply 时从已安装的那一端取


def say(msg=""):
    print(msg)


def warn(msg):
    print(msg, file=sys.stderr)


def sha8(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def hosts_of(arg):
    return list(HOSTS) if arg in (None, "all") else [arg]


def host_home(h):
    return CLAUDE_HOME if h == "claude" else CODEX_HOME


def kit_skills():
    return sorted(p for p in (KIT / "skills").iterdir() if (p / "SKILL.md").exists())


def kit_agents():
    return sorted((KIT / "agents").glob("*.md"))


def bin_links():
    """两端通用的命令入口：skills 里统一写 "$HOME/.ai-flow/bin/flowctl"，不依赖装在哪一端。"""
    return [(KIT / "skills" / "flow" / "scripts" / "flowctl.py", FLOW_HOME / "bin" / "flowctl"),
            (KIT / "workbench" / "wb.py", FLOW_HOME / "bin" / "wb"),
            (KIT, FLOW_HOME / "kit")]  # skill 通过它读 playbooks/ 和 rules/


def link_targets(h):
    """(源, 目标) 列表：两端都装 skills；agents 只有 Claude 有原生机制。"""
    home = host_home(h)
    pairs = [(p, home / "skills" / p.name) for p in kit_skills()]
    if h == "claude":
        pairs += [(p, home / "agents" / p.name) for p in kit_agents()]
    return pairs


def link_state(src, dst):
    if dst.is_symlink():
        tgt = Path(os.readlink(dst))
        if tgt == src:
            return "ok" if dst.exists() else "broken"
        return "conflict-link"
    if dst.exists():
        return "conflict-file"
    return "missing"


# ---------------------------------------------------------------- 全局规则区块

def rules_source():
    p = KIT / "rules" / "global.md"
    return p.read_text(encoding="utf-8").strip() + "\n" if p.exists() else None


def render_block(src):
    return f"<!-- ai-coding-flow:begin sha={sha8(src)} -->\n{src}<!-- ai-coding-flow:end -->\n"


def read_keep(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_keep(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def rules_state(h):
    """返回 (状态, 说明)：ok / missing / stale（源已更新）/ edited（区块被手改）/ no-source。"""
    src = rules_source()
    if src is None:
        return "no-source", "rules/global.md 不存在"
    f = RULES_FILE[h]
    text = read_keep(f) if f.exists() else ""
    m = BEGIN_RE.search(text)
    if not m and "ai-coding-flow:" in text:
        return "damaged", f"{f} 中的受管区块标记不完整，请手动修复后再 sync"
    if not m:
        return "missing", f"{f} 中没有受管区块"
    recorded, body = m.group(1), m.group(2)
    if sha8(body) != recorded:
        return "edited", f"{f} 的受管区块被手动改过（下次 sync 会被覆盖）"
    if recorded != sha8(src):
        return "stale", f"{f} 的受管区块落后于 rules/global.md"
    return "ok", str(f)


def backup(path):
    if not path.exists():
        return None
    bdir = FLOW_HOME / "backups" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir.mkdir(parents=True, exist_ok=True)
    dst = bdir / f"{path.parent.name}-{path.name}"
    shutil.copy2(path, dst)
    return dst


def write_rules(h, dry):
    src = rules_source()
    if src is None:
        return
    state, why = rules_state(h)
    if state == "ok":
        say(f"  规则  {RULES_FILE[h]}：已是最新")
        return
    if state == "damaged":
        warn(f"  规则  {why}")
        return
    f = RULES_FILE[h]
    text = read_keep(f) if f.exists() else ""
    block = render_block(src)
    new = BEGIN_RE.sub(lambda _: block, text, count=1) if BEGIN_RE.search(text) else \
        text + ("" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")) + block
    if dry:
        say(f"  规则  {f}：将{'更新' if state != 'missing' else '追加'}受管区块（dry-run）")
        return
    b = backup(f)
    f.parent.mkdir(parents=True, exist_ok=True)
    write_keep(f, new)
    say(f"  规则  {f}：已{'更新' if state != 'missing' else '追加'}受管区块" + (f"，备份在 {b}" if b else ""))


def remove_rules(h):
    f = RULES_FILE[h]
    if not f.exists():
        return
    text = read_keep(f)
    if BEGIN_RE.search(text):
        b = backup(f)
        m = BEGIN_RE.search(text)
        start = m.start() - 1 if text[max(0, m.start() - 2):m.start()] == "\n\n" else m.start()
        write_keep(f, text[:start] + text[m.end():])  # 连同追加时加的分隔空行一起删，其余字节原样保留
        say(f"  移除 {f} 的受管区块（备份在 {b}）")


# ---------------------------------------------------------------- sync / uninstall

def cmd_sync(a):
    problems = 0
    for h in hosts_of(a.host):
        say(f"[{h}]")
        if not host_home(h).exists():
            say(f"  跳过：{host_home(h)} 不存在（没装 {h}？）")
            continue
        for src, dst in link_targets(h):
            st = link_state(src, dst)
            if st == "ok":
                continue
            if st in ("conflict-file", "conflict-link"):
                warn(f"  冲突 {dst}：已存在{'指向别处的链接' if st == 'conflict-link' else '同名文件/目录'}，跳过")
                problems += 1
                continue
            if a.dry_run:
                say(f"  将链接 {dst}")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if st == "broken":
                dst.unlink()
            dst.symlink_to(src)
            say(f"  链接 {dst}")
        if not a.no_rules:
            write_rules(h, a.dry_run)
        flow = host_home(h) / "skills" / "flow"
        if not a.dry_run and link_state(KIT / "skills" / "flow", flow) != "ok":
            warn(f"  失败：{flow} 不是本仓库的链接，所有 flow-* skill 都依赖它")
            problems += 1
    for src, dst in bin_links():
        st = link_state(src, dst)
        if st in ("conflict-file", "conflict-link"):
            warn(f"冲突 {dst}：已存在，跳过")
            problems += 1
        elif st != "ok" and not a.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if st == "broken":
                dst.unlink()
            dst.symlink_to(src)
            if src.is_file():
                src.chmod(src.stat().st_mode | 0o111)
            say(f"命令入口 {dst}")
    specs = FLOW_HOME / "specs"
    if not specs.exists() and not a.dry_run:
        shutil.copytree(KIT / "specs-starter", specs)
        say(f"全局 spec 库：已用 specs-starter 初始化 {specs}")
    say("\n完成。" if not problems else f"\n完成，但有 {problems} 个问题需要处理。")
    return 1 if problems else 0


def cmd_uninstall(a):
    for h in hosts_of(a.host):
        say(f"[{h}]")
        for src, dst in link_targets(h):
            if link_state(src, dst) in ("ok", "broken"):
                dst.unlink()
                say(f"  移除 {dst}")
        remove_rules(h)
    if a.host in (None, "all"):
        for src, dst in bin_links():
            if link_state(src, dst) in ("ok", "broken"):
                dst.unlink()
                say(f"移除 {dst}")
    say("已卸载（~/.ai-flow 保留不动）")
    return 0


# ---------------------------------------------------------------- MCP

CORE = {"type", "command", "args", "env", "url"}


def with_extra(spec, raw, ignore=()):
    """headers、cwd、bearer_token_env_var 等核心字段之外的配置放进 extra，同步时不会被悄悄丢掉。"""
    extra = {k: v for k, v in raw.items() if k not in CORE and k not in ignore and v not in (None, [], {}, "", ".")}
    if extra:
        spec["extra"] = extra
    return spec


def norm_claude(cfg):
    t = cfg.get("type") or ("stdio" if cfg.get("command") else "http")
    if t == "stdio":
        spec = {"transport": "stdio", "command": cfg.get("command"), "args": cfg.get("args") or [],
                "env": cfg.get("env") or {}}
    else:
        spec = {"transport": t, "url": cfg.get("url")}
    return with_extra(spec, cfg)


def norm_codex(item):
    tr = item.get("transport") or {}
    t = tr.get("type", "stdio")
    if t == "stdio":
        spec = {"transport": "stdio", "command": tr.get("command"), "args": tr.get("args") or [],
                "env": tr.get("env") or {}}
    else:
        spec = {"transport": "http", "url": tr.get("url")}
    return with_extra(spec, tr)


def installed_mcp():
    """两端当前的 MCP（Claude 只读用户级 ~/.claude.json；claude.ai 连接器和插件自带的不在其中）。"""
    out = {"claude": {}, "codex": {}}
    try:
        d = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
        out["claude"] = {k: norm_claude(v) for k, v in (d.get("mcpServers") or {}).items()}
    except (OSError, ValueError):
        pass
    if shutil.which("codex"):
        r = subprocess.run(["codex", "mcp", "list", "--json"], capture_output=True, text=True)
        if r.returncode == 0:
            try:
                out["codex"] = {i["name"]: norm_codex(i) for i in json.loads(r.stdout)}
            except (ValueError, KeyError):
                pass
    return out


def comparable(spec):
    """比较时忽略 env 的值（只比键），避免敏感值进入比较结果。"""
    s = dict(spec)
    if "env" in s:
        s["env"] = sorted((s["env"] or {}).keys())
    if "extra" in s:
        s["extra"] = sorted(s["extra"].keys())
    for k in ("hosts", "note"):
        s.pop(k, None)
    return s


def load_manifest():
    if not MCP_MANIFEST.exists():
        return None
    return json.loads(MCP_MANIFEST.read_text(encoding="utf-8")).get("servers", {})


def cmd_mcp_import(a):
    if MCP_MANIFEST.exists() and not a.force:
        warn(f"{MCP_MANIFEST} 已存在；要从两端当前配置重新生成请加 --force")
        return 1
    inst = installed_mcp()
    servers = {}
    for h in HOSTS:
        for name, spec in inst[h].items():
            if name in servers:
                if comparable(servers[name]) == comparable(spec):
                    servers[name]["hosts"].append(h)
                else:  # 同名不同配置：只管理先出现的一端，另一端保持原样
                    servers[name]["note"] = f"{h} 端有同名但配置不同的 server，未纳入管理；需要统一请手动处理"
                    warn(f"注意：{name} 在两端配置不同，只管理 {servers[name]['hosts'][0]} 端")
                continue
            s = servers[name] = json.loads(json.dumps(spec))
            s["hosts"] = [h]
            if s.get("env"):
                s["env"] = {k: FROM_HOST for k in s["env"]}  # 值不入库
            if s.get("extra"):
                s["extra"] = {k: FROM_HOST for k in s["extra"]}
            blob = " ".join(map(str, s.get("args", []))) + " " + str(s.get("url", ""))
            if re.search(r"(token|secret|password|passwd|api[_-]?key|sk-[a-z0-9])", blob, re.I):
                warn(f"注意：{name} 的参数或 URL 里像是有凭据，mcp.json 已在 .gitignore 中，请勿提交或外传")
    MCP_MANIFEST.write_text(json.dumps({"servers": servers}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    say(f"已根据两端当前配置生成 {MCP_MANIFEST}（{len(servers)} 个 server，env 值已脱敏）")
    say("编辑每个 server 的 hosts 表示「希望它出现在哪几端」，然后运行 wb mcp diff / apply")
    return 0


def mcp_plan():
    """对比清单与实际，返回 [(host, name, 动作, 说明, spec)]。"""
    manifest = load_manifest()
    if manifest is None:
        return None, []
    inst = installed_mcp()
    plan = []
    for name, spec in sorted(manifest.items()):
        for h in spec.get("hosts", []):
            if h == "codex" and spec.get("transport") not in ("stdio", "http"):
                plan.append((h, name, "unsupported", f"codex 不支持 {spec.get('transport')} 传输", spec))
                continue
            cur = inst[h].get(name)
            if cur is None:
                if spec.get("extra"):
                    plan.append((h, name, "manual", "缺失，但含 " + "、".join(spec["extra"]) + " 等额外字段，请手动配置", spec))
                else:
                    plan.append((h, name, "add", "缺失", spec))
            elif comparable(cur) != comparable(spec):
                lost = sorted(set(cur.get("env") or {}) - set(spec.get("env") or {})) + sorted(cur.get("extra") or {})
                if lost:
                    plan.append((h, name, "manual", "配置不一致，覆盖会丢失 " + "、".join(lost) + "，请手动处理", spec))
                else:
                    plan.append((h, name, "update", "配置不一致（默认不覆盖，apply 加 --allow-update 才更新）", spec))
    unmanaged = [(h, n) for h in HOSTS for n in inst[h] if n not in manifest]
    return inst, plan + [(h, n, "unmanaged", "不在清单中（不会被改动）", None) for h, n in unmanaged]


def resolve_env(name, spec, inst):
    env = dict(spec.get("env") or {})
    for k, v in env.items():
        if v == FROM_HOST:
            src = next((inst[h][name]["env"].get(k) for h in HOSTS
                        if name in inst[h] and (inst[h][name].get("env") or {}).get(k) not in (None, FROM_HOST)), None)
            if src is None:
                raise ValueError(f"{name} 的 env {k} 在两端都没有可用的值，请在清单里手动填写或先在一端配置好")
            env[k] = src
    return env


def mcp_commands(h, name, action, spec, inst):
    env = resolve_env(name, spec, inst) if spec.get("transport") == "stdio" else {}
    cmds = []
    if h == "claude":
        if action == "update":
            cmds.append(["claude", "mcp", "remove", "-s", "user", name])
        cfg = ({"type": "stdio", "command": spec["command"], "args": spec.get("args", []), "env": env}
               if spec["transport"] == "stdio" else {"type": spec["transport"], "url": spec["url"]})
        cmds.append(["claude", "mcp", "add-json", "-s", "user", name, json.dumps(cfg, ensure_ascii=False)])
    else:
        if action == "update":
            cmds.append(["codex", "mcp", "remove", name])
        if spec["transport"] == "stdio":
            c = ["codex", "mcp", "add", name]
            for k, v in env.items():
                c += ["--env", f"{k}={v}"]
            cmds.append(c + ["--", spec["command"], *spec.get("args", [])])
        else:
            cmds.append(["codex", "mcp", "add", name, "--url", spec["url"]])
    return cmds


def shown(cmd):
    """把命令打印给人看：env 的值一律打码，含空格或 JSON 的参数加引号。"""
    out, prev = [], ""
    for x in cmd:
        if prev == "--env":
            x = x.split("=", 1)[0] + "=***"
        elif x.startswith("{"):
            cfg = json.loads(x)
            if cfg.get("env"):
                cfg["env"] = {k: "***" for k in cfg["env"]}
            x = json.dumps(cfg, ensure_ascii=False)
        out.append("'" + x + "'" if (" " in x or x.startswith("{")) else x)
        prev = cmd[len(out) - 1]
    return " ".join(out)


def cmd_mcp_diff(a):
    inst, plan = mcp_plan()
    if inst is None:
        warn("还没有 workbench/mcp.json：先运行 wb mcp import 生成，再编辑 hosts")
        return 1
    todo = [p for p in plan if p[2] in ("add", "update", "manual", "unsupported")]
    for h, name, action, why, _ in plan:
        mark = {"add": "+", "update": "~", "manual": "!", "unsupported": "!", "unmanaged": "·"}[action]
        say(f" {mark} [{h}] {name}：{why}")
    if not plan:
        say("两端 MCP 与清单一致。")
    return 1 if todo else 0


def cmd_mcp_apply(a):
    inst, plan = mcp_plan()
    if inst is None:
        warn("还没有 workbench/mcp.json：先运行 wb mcp import")
        return 1
    rc = 0
    for h, name, action, why, spec in plan:
        if action not in ("add", "update") or h not in hosts_of(a.host):
            continue
        if action == "update" and not a.allow_update:
            say(f"\n[{h}] {name}：{why}")
            continue
        try:
            cmds = mcp_commands(h, name, action, spec, inst)
        except ValueError as e:
            warn(f"跳过 [{h}] {name}：{e}")
            rc = 1
            continue
        say(f"\n[{h}] {name}（{why}）将执行：")
        for c in cmds:
            say("  " + shown(c))
        if not a.yes and input("  执行？[y/N] ").strip().lower() != "y":
            say("  已跳过")
            continue
        for c in cmds:
            r = subprocess.run(c, capture_output=True, text=True)
            if r.returncode != 0:
                warn(f"  失败：{(r.stderr or r.stdout).strip()[-300:]}")
                rc = 1
                break
        else:
            say("  完成")
    return rc


# ---------------------------------------------------------------- doctor / diff

def cli_version(name):
    if not shutil.which(name):
        return None, "未安装"
    try:
        r = subprocess.run([name, "--version"], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None, "--version 超时"
    out = (r.stdout or r.stderr).strip().splitlines()
    return (out[0] if out else "?") if r.returncode == 0 else None, (out[0] if out else f"退出码 {r.returncode}")


def cmd_doctor(a):
    issues = 0
    say(f"kit：{KIT}")
    say(f"python {sys.version.split()[0]}  git {'可用' if shutil.which('git') else '缺失'}")
    for h in HOSTS:
        ver, err = cli_version(h)
        say(f"\n[{h}]  CLI：{ver or '✗ ' + err}")
        if not ver:
            issues += 1
        if not host_home(h).exists():
            say(f"  {host_home(h)} 不存在")
            continue
        for src, dst in link_targets(h):
            st = link_state(src, dst)
            if st != "ok":
                issues += 1
                say(f"  ✗ {dst.relative_to(host_home(h))}：{st}")
        st, why = rules_state(h)
        if st != "ok":
            issues += 1
        say(f"  {'✓' if st == 'ok' else '✗'} 规则区块：{st}（{why}）")
    for src, dst in bin_links():
        st = link_state(src, dst)
        say(f"{'✓' if st == 'ok' else '✗'} 命令入口 {dst}：{st}")
        issues += 0 if st == "ok" else 1
    specs = FLOW_HOME / "specs"
    say(f"\n全局 spec 库：{'✓ ' + str(specs) if specs.exists() else '✗ 未初始化（wb sync 会创建）'}")
    issues += 0 if specs.exists() else 1
    if MCP_MANIFEST.exists():
        _, plan = mcp_plan()
        pending = [p for p in plan if p[2] in ("add", "update", "manual", "unsupported")]
        say(f"MCP：{'✓ 与清单一致' if not pending else f'✗ {len(pending)} 处与清单不一致（wb mcp diff 查看）'}")
        issues += 1 if pending else 0
    else:
        say("MCP：还没有清单（wb mcp import 生成）")
    say(f"\n{'一切正常' if not issues else f'共 {issues} 个问题'}")
    return 1 if issues else 0


def cmd_diff(a):
    names = {h: {p.name for p in (host_home(h) / "skills").iterdir() if not p.name.startswith(".")}
             if (host_home(h) / "skills").exists() else set() for h in HOSTS}
    only_c, only_x = sorted(names["claude"] - names["codex"]), sorted(names["codex"] - names["claude"])
    kit = {p.name for p in kit_skills()}
    say("skills 只在 Claude 端：" + (", ".join(only_c) or "无"))
    say("skills 只在 Codex 端：" + (", ".join(only_x) or "无"))
    both = sorted((names["claude"] & names["codex"]) - kit)
    dup = [n for n in both if not (host_home("claude") / "skills" / n).is_symlink()
           and not (host_home("codex") / "skills" / n).is_symlink()]
    if dup:
        say("两端各放了一份拷贝（可能已经不一致，建议收进工作台统一管理）：" + ", ".join(dup))
    for h in HOSTS:
        st, why = rules_state(h)
        say(f"规则区块 [{h}]：{st}")
    if MCP_MANIFEST.exists():
        say("MCP：")
        cmd_mcp_diff(a)
    return 0


def main():
    p = argparse.ArgumentParser(prog="wb", description="个人 AI 工作台")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="把 skills、agents、全局规则同步到两端")
    s.add_argument("--host", choices=HOSTS + ("all",), default="all")
    s.add_argument("--no-rules", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("uninstall", help="移除 wb 装的链接和规则区块")
    s.add_argument("--host", choices=HOSTS + ("all",), default="all")
    sub.add_parser("doctor", help="体检")
    sub.add_parser("diff", help="两端差异")
    s = sub.add_parser("mcp", help="MCP 清单")
    ms = s.add_subparsers(dest="mcmd", required=True)
    mi = ms.add_parser("import")
    mi.add_argument("--force", action="store_true")
    ms.add_parser("diff")
    ma = ms.add_parser("apply")
    ma.add_argument("--host", choices=HOSTS + ("all",), default="all")
    ma.add_argument("--yes", action="store_true", help="不逐条确认（慎用）")
    ma.add_argument("--allow-update", action="store_true", help="允许覆盖配置不一致的 server（不会丢字段时才执行）")
    a = p.parse_args()
    if a.cmd == "mcp":
        fn = {"import": cmd_mcp_import, "diff": cmd_mcp_diff, "apply": cmd_mcp_apply}[a.mcmd]
    else:
        fn = {"sync": cmd_sync, "uninstall": cmd_uninstall, "doctor": cmd_doctor, "diff": cmd_diff}[a.cmd]
    sys.exit(fn(a))


if __name__ == "__main__":
    main()
