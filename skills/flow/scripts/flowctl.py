#!/usr/bin/env python3
"""flowctl：个人 AI Coding 流程的状态、spec 召回、快照与度量工具。

只依赖 Python 3.9+ 标准库和 git。所有状态放在目标仓库的 .ai/ 下：

  .ai/config.json          阈值配置
  .ai/specs/{tech,domain,dev,review}/*.md   spec 库（带 frontmatter）
  .ai/specs/_scores.jsonl  每次使用后的打分记录
  .ai/changes/<日期-slug>/ 一次变更的全部过程档案
  .ai/current              当前进行中的变更名
  .ai/kb/                  代码知识库：scan.json、plan.json、各页 .md、index.md、_manifest.json（见 kb.py）

快照存成隐藏 ref：refs/flow/<变更>/<任务>/{base,v1,final}，不动真实暂存区和分支。
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb  # noqa: E402  代码知识库子命令

SPEC_TYPES = ("tech", "domain", "dev", "review")
STAGES = ("proposal", "design", "tasks", "build", "review", "retro")
EVENTS = ("new", "generated", "accepted", "rejected", "started", "done", "note", "closed")
VERDICTS = ("hit", "unused", "mislead")
PHASES = ("base", "v1", "final")
STAGE_FILES = ("proposal.md", "design.md", "tasks.md", "review.md", "retro.md")
TODO_MARK = "<!-- flow:todo -->"
KIT_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = KIT_DIR / "templates"
EXCLUDE = [":(exclude).ai"]
DEFAULT_CONFIG = {
    "lane_step_threshold": 2,
    "global_spec_dir": os.path.join(os.environ.get("AI_FLOW_HOME") or "~/.ai-flow", "specs"),
    "recall_top": 8,
    "revise_min_uses": 3,
    "revise_mislead_rate": 0.2,
    "revise_hit_rate": 0.3,
    "retire_idle_recalls": 10,
}

ROOT = None  # 仓库根目录，git() 在这里执行


def die(msg):
    print(f"flowctl: {msg}", file=sys.stderr)
    sys.exit(1)


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def git(*args, env=None, check=True):
    r = subprocess.run(["git", *args], capture_output=True, encoding="utf-8", errors="replace",
                       env=env, cwd=ROOT)
    if check and r.returncode != 0:
        die(f"git {' '.join(args)} 失败：{r.stderr.strip()}")
    return r.stdout


def is_ancestor(a, b):
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=ROOT,
                          capture_output=True).returncode == 0


def rev(ref):
    r = subprocess.run(["git", "rev-parse", "--verify", "-q", ref + "^{commit}"],
                       capture_output=True, text=True, cwd=ROOT)
    return r.stdout.strip() if r.returncode == 0 else None


# ---------------------------------------------------------------- 上下文

class Ctx:
    def __init__(self):
        global ROOT
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if r.returncode != 0:
            die("当前目录不在 git 仓库里")
        ROOT = Path(r.stdout.strip())
        self.root = ROOT
        self.ai = ROOT / ".ai"
        self.cfg = dict(DEFAULT_CONFIG)
        cf = self.ai / "config.json"
        if cf.exists():
            self.cfg.update(json.loads(cf.read_text(encoding="utf-8")))

    def need_init(self):
        if not self.ai.is_dir():
            die("还没初始化，先运行 flowctl init")

    def current_name(self):
        p = self.ai / "current"
        return p.read_text(encoding="utf-8").strip() if p.exists() else None

    def change(self, name=None):
        self.need_init()
        name = name or self.current_name()
        if not name:
            die("没有进行中的变更，先 flowctl new <slug>")
        d = self.ai / "changes" / name
        if not d.is_dir():
            die(f"变更不存在：{name}")
        return d


def append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def trace(change_dir, stage, event, **extra):
    rec = {"ts": now(), "stage": stage, "event": event}
    rec.update({k: v for k, v in extra.items() if v is not None})
    append_jsonl(change_dir / "trace.jsonl", rec)


# ---------------------------------------------------------------- spec 解析

def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def _strip_comment(s):
    """去掉行尾 # 注释；引号内的 # 保留。"""
    q = None
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#" and i > 0 and s[i - 1] in " \t":
            return s[:i]
    return s


def parse_frontmatter(text):
    """解析 YAML 的一个小子集：key: value、key: [a, b]、以及 - 列表项。"""
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    meta, key = {}, None
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), _strip_comment(m.group(2)).strip()
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                meta[key] = [_unquote(x) for x in next(csv.reader([inner], skipinitialspace=True))
                             if x.strip()] if inner else []
            elif val == "":
                meta[key] = []
            else:
                meta[key] = _unquote(val)
        elif key and re.match(r"^\s*-\s+", line):
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(_unquote(_strip_comment(re.sub(r"^\s*-\s+", "", line))))
    return meta, body


def _as_list(v):
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


class Spec:
    def __init__(self, path, scope):
        self.path = path
        self.scope = scope
        self.meta, self.body = parse_frontmatter(path.read_text(encoding="utf-8"))
        self.id = str(self.meta.get("id") or path.stem)
        parent = path.parent.name
        self.type = str(self.meta.get("type") or (parent if parent in SPEC_TYPES else "dev"))
        self.triggers = [t for t in _as_list(self.meta.get("triggers")) if t]
        self.paths = [p for p in _as_list(self.meta.get("paths")) if p]
        self.when = str(self.meta.get("when") or "").strip()
        self.status = str(self.meta.get("status") or "active")
        self.always = str(self.meta.get("always", "")).lower() in ("true", "yes", "on")


def spec_dirs(ctx):
    dirs = [(ctx.ai / "specs", "repo")]
    g = ctx.cfg.get("global_spec_dir")
    if g:
        gp = Path(os.path.expanduser(g))
        if gp.is_dir() and gp.resolve() != (ctx.ai / "specs").resolve():
            dirs.append((gp, "global"))
    return dirs


def load_specs(ctx):
    """先读仓库级，再读全局；全局里与仓库级同 id 的条目被仓库级覆盖。"""
    specs, repo_ids = [], set()
    for d, scope in spec_dirs(ctx):
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.md")):
            if p.name.startswith("_") or p.name.lower() == "readme.md":
                continue
            s = Spec(p, scope)
            if scope == "global" and s.id in repo_ids:
                continue
            if scope == "repo":
                repo_ids.add(s.id)
            specs.append(s)
    return specs


def glob_match(path, pat):
    """路径 glob：** 跨目录（可匹配零层），* 和 ? 不跨 /。"""
    rx, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            rx, i = rx + "(?:.*/)?", i + 3
        elif pat.startswith("**", i):
            rx, i = rx + ".*", i + 2
        elif pat[i] == "*":
            rx, i = rx + "[^/]*", i + 1
        elif pat[i] == "?":
            rx, i = rx + "[^/]", i + 1
        else:
            rx, i = rx + re.escape(pat[i]), i + 1
    return re.fullmatch(rx, path) is not None


def trigger_hit(trigger, text):
    if re.fullmatch(r"[A-Za-z0-9_.\-/ ]+", trigger):
        pat = r"(?<![A-Za-z0-9_])" + re.escape(trigger) + r"(?![A-Za-z0-9_])"
        return re.search(pat, text, re.I) is not None
    return trigger.lower() in text.lower()


def spec_stats(ctx):
    """按 spec 汇总打分：使用次数、命中率、误导率、最近连续未命中次数。"""
    by = {}
    for r in read_jsonl(ctx.ai / "specs" / "_scores.jsonl"):
        by.setdefault(r["spec"], []).append(r["verdict"])
    stats = {}
    for sid, vs in by.items():
        idle = 0
        for v in reversed(vs):
            if v == "hit":
                break
            idle += 1
        n = len(vs)
        stats[sid] = {"uses": n, "hit_rate": vs.count("hit") / n,
                      "mislead_rate": vs.count("mislead") / n, "idle": idle}
    return stats


# ---------------------------------------------------------------- 命令：生命周期

def cmd_init(ctx, a):
    for t in SPEC_TYPES:
        (ctx.ai / "specs" / t).mkdir(parents=True, exist_ok=True)
    (ctx.ai / "changes").mkdir(parents=True, exist_ok=True)
    (ctx.ai / "specs" / "_scores.jsonl").touch()
    cf = ctx.ai / "config.json"
    if not cf.exists():
        cf.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tpl = TEMPLATE_DIR / "spec.md"
    if tpl.exists():
        shutil.copy(tpl, ctx.ai / "specs" / "_template.md")
    if a.local_only:
        ex = Path(git("rev-parse", "--git-path", "info/exclude").strip())
        ex = ex if ex.is_absolute() else ctx.root / ex
        ex.parent.mkdir(parents=True, exist_ok=True)
        cur = ex.read_text(encoding="utf-8") if ex.exists() else ""
        if ".ai/" not in cur.split():
            ex.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n") + ".ai/\n", encoding="utf-8")
        print("已把 .ai/ 加入 .git/info/exclude（只在本地保留，不会被提交）")
    print(f"已初始化 {ctx.ai}")
    print("下一步：往 .ai/specs/<类型>/ 里放 spec（模板见 .ai/specs/_template.md），然后 flowctl new <slug>")


def cmd_new(ctx, a):
    ctx.need_init()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,48}", a.slug):
        die("slug 只能用小写字母、数字和连字符，2–49 个字符")
    name = f"{dt.date.today():%Y%m%d}-{a.slug}"
    d = ctx.ai / "changes" / name
    if d.exists():
        die(f"已存在：{name}")
    d.mkdir(parents=True)
    title = a.title or a.slug
    for fn in STAGE_FILES:
        src = TEMPLATE_DIR / fn
        if src.exists():
            txt = src.read_text(encoding="utf-8")
            txt = txt.replace("{{change}}", name).replace("{{title}}", title).replace("{{date}}", str(dt.date.today()))
            (d / fn).write_text(txt, encoding="utf-8")
    (ctx.ai / "current").write_text(name + "\n", encoding="utf-8")
    trace(d, "proposal", "new", note=title)
    print(f"已创建变更 {name}，并设为当前变更")
    print(f"  {d.relative_to(ctx.root)}/")


def cmd_use(ctx, a):
    d = ctx.change(a.change)
    (ctx.ai / "current").write_text(d.name + "\n", encoding="utf-8")
    print(f"当前变更：{d.name}")


def cmd_list(ctx, a):
    ctx.need_init()
    cur = ctx.current_name()
    for d in sorted((ctx.ai / "changes").iterdir()):
        if d.is_dir():
            closed = any(r["event"] == "closed" for r in read_jsonl(d / "trace.jsonl"))
            mark = "*" if d.name == cur else " "
            print(f"{mark} {d.name}{'  (已关闭)' if closed else ''}")


def cmd_close(ctx, a):
    d = ctx.change(a.change)
    trace(d, "retro", "closed")
    if ctx.current_name() == d.name:
        (ctx.ai / "current").unlink()
    print(f"已关闭 {d.name}")


def task_progress(d):
    p = d / "tasks.md"
    if not p.exists():
        return 0, 0
    txt = p.read_text(encoding="utf-8")
    done = len(re.findall(r"^\s*-\s*\[[xX]\]\s*T\d+", txt, re.M))
    todo = len(re.findall(r"^\s*-\s*\[ \]\s*T\d+", txt, re.M))
    return done, done + todo


def load_snapshots(d):
    p = d / "snapshots.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def next_step(d):
    """根据产物状态推断下一步应该跑哪个 skill。"""
    def filled(fn):
        p = d / fn
        return p.exists() and TODO_MARK not in p.read_text(encoding="utf-8")
    if not filled("proposal.md"):
        return "/flow-propose"
    if not filled("design.md"):
        return "/flow-design"
    if not filled("tasks.md"):
        return "/flow-tasks"
    done, total = task_progress(d)
    if total == 0 or done < total:
        return "/flow-build"
    if not filled("review.md"):
        return "/flow-review"
    if not filled("retro.md"):
        return "/flow-retro"
    return "flowctl close（已全部完成）"


def cmd_status(ctx, a):
    d = ctx.change(a.change)
    print(f"变更：{d.name}")
    for fn in STAGE_FILES:
        p = d / fn
        state = "缺失" if not p.exists() else ("待填写" if TODO_MARK in p.read_text(encoding="utf-8") else "已完成")
        print(f"  {fn:<12} {state}")
    done, total = task_progress(d)
    print(f"  任务进度     {done}/{total}")
    snaps = load_snapshots(d)
    if snaps:
        print("  快照         " + "  ".join(f"{t}:{'/'.join(p for p in PHASES if p in v)}" for t, v in sorted(snaps.items())))
    recalled = d / "recalled.json"
    if recalled.exists():
        print(f"  召回 spec    {len(json.loads(recalled.read_text(encoding='utf-8')))} 条")
    print(f"  {kb.status_line(ctx)}")
    print(f"下一步：{next_step(d)}")


def cmd_trace(ctx, a):
    d = ctx.change(a.change)
    trace(d, a.stage, a.event, task=a.task, note=a.note)
    print(f"已记录 {a.stage}/{a.event}")


# ---------------------------------------------------------------- 命令：spec

def cmd_recall(ctx, a):
    ctx.need_init()
    text = a.text or ""
    for f in a.file or []:
        text += "\n" + Path(f).read_text(encoding="utf-8")
    paths = a.paths or []
    if not text.strip() and not paths:
        die("需要 --text、--file 或 --paths 中至少一个")
    stats = spec_stats(ctx)
    results = []
    for s in load_specs(ctx):
        if s.status == "retired" or s.always:
            continue  # 常驻的 review 检查项由 verifier 每次必查，不参与召回
        hits = [t for t in s.triggers if trigger_hit(t, text)]
        phits = [p for p in s.paths if any(glob_match(x, p) for x in paths)]
        if not (hits or phits):
            continue
        score = len(hits) + 2 * len(phits)
        st = stats.get(s.id)
        if st and st["uses"] >= ctx.cfg["revise_min_uses"]:
            score *= 0.5 + st["hit_rate"]  # 历史命中率高的往前排
        results.append({"id": s.id, "type": s.type, "scope": s.scope, "score": round(score, 2),
                        "path": str(s.path), "when": s.when, "triggers": hits, "paths": phits,
                        "status": s.status})
    results.sort(key=lambda r: -r["score"])
    top = results[: a.top if a.top is not None else ctx.cfg["recall_top"]]
    if a.json:
        print(json.dumps(top, ensure_ascii=False, indent=2))
    else:
        if not top:
            print("没有召回到 spec。")
        for r in top:
            why = []
            if r["triggers"]:
                why.append("关键词 " + "/".join(r["triggers"]))
            if r["paths"]:
                why.append("路径 " + "/".join(r["paths"]))
            flag = "" if r["status"] == "active" else f" [{r['status']}]"
            print(f"{r['score']:>5}  {r['type']:<6} {r['id']}{flag}  ← {'；'.join(why)}")
            print(f"       何时注入：{r['when'] or '（未写）'}")
            print(f"       {r['path']}")
    if a.record:
        d = ctx.change()
        p = d / "recalled.json"
        prev = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        ids = {r["id"] for r in prev}
        prev += [r for r in top if r["id"] not in ids]
        p.write_text(json.dumps(prev, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        trace(d, "proposal", "note", note=f"recall {len(top)} specs")


def cmd_score(ctx, a):
    ctx.need_init()
    known = {s.id for s in load_specs(ctx)}
    if a.spec not in known:
        print(f"警告：spec {a.spec} 不在库里，照样记录", file=sys.stderr)
    change = ctx.current_name()
    append_jsonl(ctx.ai / "specs" / "_scores.jsonl",
                 {"ts": now(), "change": change, "spec": a.spec, "verdict": a.verdict, "note": a.note})
    print(f"已记录 {a.spec} → {a.verdict}")


def cmd_specs_lint(ctx, a):
    ctx.need_init()
    errors = warns = 0
    seen = {}
    for s in load_specs(ctx):
        rel = s.path
        if s.id in seen:  # 仓库级覆盖全局是允许的，这里只剩同一层级内的重复
            print(f"ERROR {rel}: id {s.id} 与 {seen[s.id]} 重复"); errors += 1
        seen[s.id] = rel
        if s.when in (">", "|", ">-", "|-"):
            print(f"ERROR {rel}: when 不支持多行写法，请写成一行"); errors += 1
        elif not s.when:
            print(f"ERROR {rel}: 缺少 when —— 说不清何时注入的 spec 就是噪声"); errors += 1
        if not (s.triggers or s.paths or s.always):
            print(f"ERROR {rel}: triggers / paths / always 至少要有一个，否则永远召回不到"); errors += 1
        if s.always and s.type != "review":
            print(f"WARN  {rel}: 非 review 类 spec 设为常驻，会稀释注意力"); warns += 1
        if s.type not in SPEC_TYPES:
            print(f"WARN  {rel}: type={s.type} 不在 {SPEC_TYPES} 里"); warns += 1
        for t in s.triggers:
            if len(t) < 2:
                print(f"WARN  {rel}: 触发词 '{t}' 太短，会误召回"); warns += 1
        if len(s.body) > 6000:
            print(f"WARN  {rel}: 正文 {len(s.body)} 字符，建议拆小"); warns += 1
    print(f"{len(seen)} 条 spec，{errors} 个错误，{warns} 个警告")
    if errors:
        sys.exit(1)


def cmd_specs_report(ctx, a):
    ctx.need_init()
    stats = spec_stats(ctx)
    c = ctx.cfg
    rows = []
    for s in load_specs(ctx):
        st = stats.get(s.id, {"uses": 0, "hit_rate": 0, "mislead_rate": 0, "idle": 0})
        flag = ""
        if s.status == "retired":
            flag = "已下架"
        elif st["uses"] >= c["revise_min_uses"] and st["mislead_rate"] >= c["revise_mislead_rate"]:
            flag = "进 backlog 修订（误导）"
        elif s.always:
            flag = ""  # 常驻检查项没问题时本来就是 unused，不按命中率考核
        elif st["idle"] >= c["retire_idle_recalls"]:
            flag = "建议下架（长期不命中）"
        elif st["uses"] >= c["revise_min_uses"] and st["hit_rate"] < c["revise_hit_rate"]:
            flag = "进 backlog 修订（命中低）"
        rows.append((s.id, s.type, st["uses"], st["hit_rate"], st["mislead_rate"], st["idle"], flag))
    rows.sort(key=lambda r: (r[6] == "", -r[2]))
    print(f"{'spec':<32}{'类型':<8}{'使用':>5}{'命中率':>8}{'误导率':>8}{'连续未中':>8}  处理")
    for r in rows:
        print(f"{r[0]:<32}{r[1]:<8}{r[2]:>5}{r[3]:>8.0%}{r[4]:>8.0%}{r[5]:>8}  {r[6]}")


# ---------------------------------------------------------------- 命令：快照

def worktree_commit(msg):
    """把整个工作区（含未跟踪文件，尊重 .gitignore，排除 .ai/）写成一个游离 commit。
    用临时 index，不碰真实暂存区。"""
    head = rev("HEAD")
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
        if head:
            git("read-tree", "HEAD", env=env)
        git("add", "-A", "--", ".", *EXCLUDE, env=env)
        tree = git("write-tree", env=env).strip()
    parent = ["-p", head] if head else []
    return git("-c", "user.name=flowctl", "-c", "user.email=flowctl@localhost",
               "commit-tree", tree, *parent, "-m", msg).strip()


def cmd_snapshot(ctx, a):
    d = ctx.change(a.change)
    if not re.fullmatch(r"T\d+", a.task):
        die("任务号格式应为 T1、T2 …")
    sha = worktree_commit(f"flow {d.name} {a.task} {a.phase}")
    ref = f"refs/flow/{d.name}/{a.task}/{a.phase}"
    git("update-ref", ref, sha)
    snaps = load_snapshots(d)
    snaps.setdefault(a.task, {})[a.phase] = sha
    (d / "snapshots.json").write_text(json.dumps(snaps, indent=2) + "\n", encoding="utf-8")
    trace(d, "build", {"base": "started", "v1": "generated", "final": "accepted"}[a.phase], task=a.task)
    print(f"{a.task} {a.phase} → {sha[:10]}  ({ref})")


def cmd_checkpoint(ctx, a):
    """把当前工作区（含未跟踪文件）存成 refs/flow/<变更>/checkpoint，供审查做 diff 终点。"""
    d = ctx.change(a.change)
    sha = worktree_commit(f"flow {d.name} checkpoint")
    git("update-ref", f"refs/flow/{d.name}/checkpoint", sha)
    snaps = load_snapshots(d)
    start = next((snaps[t]["base"] for t in snaps if snaps[t].get("base")), None)
    print(f"终点 refs/flow/{d.name}/checkpoint = {sha}")
    if start:
        print(f"起点（最早开工的任务 base）= {start}")
        print(f"审查 diff：git diff {start[:12]} {sha[:12]} -- . ':(exclude).ai'")


# ---------------------------------------------------------------- 命令：度量

def _cunquote(p):
    """还原 git 对特殊字符路径的 C 风格引号：\"a\\tb\" -> a<TAB>b。"""
    if not (len(p) >= 2 and p[0] == p[-1] == '"'):
        return p
    raw = p[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
    return raw.encode("latin-1", "replace").decode("utf-8", "replace")


def diff_lines(a, b):
    """diff(a, b) 的新增行（Counter[(文件, 去空白后的行)]）与删除行数。

    空行、子模块指针不计。按 diff 头 / hunk 状态机解析，内容以 +++ 或 --- 开头的行不会被误判为文件头。"""
    out = git("-c", "core.quotePath=false", "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false",
              "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--ignore-submodules",
              "--src-prefix=a/", "--dst-prefix=b/", "-U0", a, b, "--", ".", *EXCLUDE)
    added, deleted, path, in_hunk = Counter(), 0, None, False
    for line in out.splitlines():
        if line.startswith("diff --git "):
            path, in_hunk = None, False
        elif not in_hunk:
            if line.startswith("+++ "):
                name = _cunquote(line[4:].rstrip("\t"))
                path = name[2:] if name.startswith("b/") else None
            elif line.startswith("@@"):
                in_hunk = True
        elif line.startswith("@@"):
            continue
        elif line.startswith("+"):
            t = line[1:].strip()
            if t and path and not t.startswith("Subproject commit "):
                added[(path, t)] += 1
        elif line.startswith("-"):
            t = line[1:].strip()
            if t and not t.startswith("Subproject commit "):
                deleted += 1
    return added, deleted


def task_metrics(snap):
    base, v1, final = snap.get("base"), snap.get("v1"), snap.get("final")
    if not (base and v1 and final):
        return None
    v1_add, _ = diff_lines(base, v1)
    final_add, _ = diff_lines(base, final)
    rewritten, discarded = diff_lines(v1, final)
    v1_n, final_n, rw_n = sum(v1_add.values()), sum(final_add.values()), sum(rewritten.values())
    fpy = max(0.0, (final_n - rw_n) / final_n) if final_n else None
    return {"v1_lines": v1_n, "final_lines": final_n, "rewritten_lines": rw_n,
            "discarded_lines": discarded, "fpy": fpy,
            "discard_rate": (discarded / v1_n) if v1_n else None}


def adoption(d):
    by = {}
    for r in read_jsonl(d / "trace.jsonl"):
        s = by.setdefault(r["stage"], {"generated": 0, "accepted": 0, "rejected": 0})
        if r["event"] in s:
            s[r["event"]] += 1
    return {k: v for k, v in by.items() if v["generated"] or v["accepted"] or v["rejected"]}


def pct(x):
    return "  —  " if x is None else f"{x:>5.0%}"


def cmd_metrics(ctx, a):
    d = ctx.change(a.change)
    snaps = load_snapshots(d)
    tasks, w_sum, w_fpy = {}, 0, 0.0
    ai_pool = Counter()
    # snapshots.json 按首次打快照的顺序保存，第一个有 base 的任务就是最早开工的
    bases = [snaps[t]["base"] for t in snaps if snaps[t].get("base")]
    finals = [snaps[t]["final"] for t in snaps if snaps[t].get("final")]
    for t in sorted(snaps, key=lambda x: int(x[1:])):
        s = snaps[t]
        if s.get("base") and s.get("v1"):
            ai_pool += diff_lines(s["base"], s["v1"])[0]
        m = task_metrics(s)
        tasks[t] = m
        if m and m["fpy"] is not None:
            w_sum += m["final_lines"]
            w_fpy += m["fpy"] * m["final_lines"]
    result = {"change": d.name, "computed_at": now(), "tasks": tasks,
              "fpy_weighted": (w_fpy / w_sum) if w_sum else None, "adoption": adoption(d)}

    to, to_sha, fallback = a.to or "HEAD", None, False
    if bases and ai_pool:
        to_sha = rev(to)
        if to_sha and not a.to and is_ancestor(to_sha, bases[0]):
            # HEAD 还停在开工前：任务没提交，退回用最后一个 final 快照
            to, to_sha, fallback = "最后一个 final 快照（尚未提交）", (finals[-1] if finals else None), True
        elif to_sha and to == "HEAD" and git("status", "--porcelain", "--", ".", *EXCLUDE).strip():
            print("注意：工作区有未提交改动，AI 代码占比只按已提交的 HEAD 计算", file=sys.stderr)
        if not to_sha:
            print(f"注意：找不到 {to}，跳过 AI 代码占比", file=sys.stderr)
    if to_sha and a.squash:
        start = rev(to_sha + "^") or die("--squash 需要终点提交有父提交")
    else:
        start = bases[0] if bases else None
    if to_sha:
        total, _ = diff_lines(start, to_sha)
        matched = sum(min(n, ai_pool[k]) for k, n in total.items())
        total_n = sum(total.values())
        result["ai_ratio"] = {"to": to, "ai_lines": matched, "total_added": total_n,
                              "ratio": (matched / total_n) if total_n else None}
        # 供 aftercare / locate 使用：AI 行签名、涉及文件、属于本变更的提交
        result["ai_signature"] = [[k[0], k[1], min(n, ai_pool[k])] for k, n in sorted(total.items()) if ai_pool.get(k)]
        result["files"] = sorted({k[0] for k in total})
        if a.squash:
            result["commits"], result["to_sha"] = [to_sha], to_sha  # 只有 squash 提交本身属于本变更
        elif not fallback:
            parent = rev(bases[0] + "^")
            rng = [f"{parent}..{to_sha}"] if parent else [to_sha]
            result["commits"] = git("rev-list", *rng).split()
            result["to_sha"] = to_sha
    (d / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"变更 {d.name}")
    print(f"{'任务':<6}{'v1行':>7}{'终版行':>7}{'被改行':>7}{'丢弃行':>7}{'FPY':>7}{'丢弃率':>7}")
    for t, m in tasks.items():
        if not m:
            print(f"{t:<6}  （快照不全，需要 base/v1/final）")
            continue
        print(f"{t:<6}{m['v1_lines']:>7}{m['final_lines']:>7}{m['rewritten_lines']:>7}"
              f"{m['discarded_lines']:>7}{pct(m['fpy']):>7}{pct(m['discard_rate']):>7}")
    print(f"按行加权 FPY：{pct(result['fpy_weighted']).strip()}")
    if "ai_ratio" in result:
        r = result["ai_ratio"]
        print(f"AI 代码占比（到 {r['to']}）：{r['ai_lines']}/{r['total_added']} = {pct(r['ratio']).strip()}")
    if result["adoption"]:
        print("生成 / 采纳 / 驳回：")
        for st, v in result["adoption"].items():
            print(f"  {st:<9} {v['generated']} / {v['accepted']} / {v['rejected']}")


def cmd_report(ctx, a):
    ctx.need_init()
    rows = []
    for d in sorted((ctx.ai / "changes").iterdir()):
        p = d / "metrics.json"
        if p.exists():
            m = json.loads(p.read_text(encoding="utf-8"))
            ai = (m.get("ai_ratio") or {}).get("ratio")
            gen = sum(v["generated"] for v in m.get("adoption", {}).values())
            acc = sum(v["accepted"] for v in m.get("adoption", {}).values())
            metas = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((d / "review").glob("round-*.json"))]
            valid = [x for x in metas if x.get("verdict") in ("PASS", "REJECT")]
            levels = "/".join(sorted({x["level"] for x in valid}))
            rounds = f"{len(valid)} {levels}" if valid else "—"
            rows.append((d.name, m.get("fpy_weighted"), ai, f"{acc}/{gen}", rounds))
    if not rows:
        print("还没有算过度量，先对变更跑 flowctl metrics")
        return
    print(f"{'变更':<36}{'FPY':>7}{'AI占比':>8}{'采纳/生成':>10}{'审查轮次/级别':>10}")
    for r in rows:
        print(f"{r[0]:<36}{pct(r[1]):>7}{pct(r[2]):>8}{r[3]:>10}{r[4]:>10}")


# ---------------------------------------------------------------- 模型选择与角色调度
#
# 模型不写死：每次用到 scout / verifier 时，skill 先用 `flowctl models` 拿到可选项，
# 让用户选，再把选择传给 `flowctl agent` / `flowctl verify`。上一次的选择记在
# ~/.ai-flow/last_models.json，供下次作为默认选项。

KIT_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = KIT_ROOT / "agents"
FLOW_HOME = Path(os.environ.get("AI_FLOW_HOME") or os.path.expanduser("~/.ai-flow"))
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))
HOSTS = ("claude", "codex")
ROLES = ("scout", "verifier")
CLAUDE_MODELS = [
    {"model": "fable", "note": "Fable 系列"},
    {"model": "opus", "note": "Opus 系列"},
    {"model": "sonnet", "note": "Sonnet 系列"},
    {"model": "haiku", "note": "Haiku 系列，快、便宜"},
]


def codex_config():
    """读 ~/.codex/config.toml 顶层的 model 与 model_reasoning_effort（只解析第一个 [section] 之前）。"""
    out = {}
    p = CODEX_HOME / "config.toml"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("["):
            break
        m = re.match(r'^\s*(model|model_reasoning_effort)\s*=\s*"([^"]*)"', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def codex_models():
    p = CODEX_HOME / "models_cache.json"
    models = []
    try:
        for m in json.loads(p.read_text(encoding="utf-8")).get("models", []):
            if m.get("visibility", "list") != "list":
                continue
            models.append({"model": m.get("slug"), "note": m.get("description", ""),
                           "reasoning": [r.get("effort") for r in m.get("supported_reasoning_levels", [])],
                           "default_reasoning": m.get("default_reasoning_level")})
    except (OSError, ValueError, AttributeError):
        pass
    cfg = codex_config()
    if cfg.get("model") and cfg["model"] not in {m["model"] for m in models}:
        models.insert(0, {"model": cfg["model"], "note": "config.toml 当前默认模型",
                          "reasoning": [], "default_reasoning": cfg.get("model_reasoning_effort")})
    return models


def load_last():
    p = FLOW_HOME / "last_models.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_last(role, choice):
    """记住上次选择只是便利功能：沙箱里写不了 ~/.ai-flow 时静默跳过。"""
    try:
        FLOW_HOME.mkdir(parents=True, exist_ok=True)
        last = load_last()
        last[role] = choice
        (FLOW_HOME / "last_models.json").write_text(json.dumps(last, ensure_ascii=False, indent=2) + "\n",
                                                    encoding="utf-8")
    except OSError:
        pass


def cmd_models(ctx, a):
    info = {
        "role": a.role,
        "cli_available": {h: shutil.which(h) is not None for h in HOSTS},
        "claude": CLAUDE_MODELS,
        "codex": codex_models(),
        "codex_default": codex_config(),
        "last": load_last().get(a.role),
    }
    if a.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return
    print(f"角色：{a.role}")
    if info["last"]:
        l = info["last"]
        print(f"上次选择：{l['engine']} / {l['model']}" + (f" / {l['reasoning']}" if l.get("reasoning") else ""))
    for h in HOSTS:
        print(f"\n[{h}]  CLI {'可用' if info['cli_available'][h] else '不可用'}")
        for m in info[h]:
            extra = f"  推理档位：{'/'.join(x for x in m.get('reasoning') or [] if x)}" if m.get("reasoning") else ""
            print(f"  {m['model']:<16} {m['note']}{extra}")


def independence(host, engine, model, host_model):
    if engine != host:
        return "L3"
    if host_model and model and host_model != model:
        return "L2"
    return "L1"


def role_prompt(role, extra):
    """agents/flow-<role>.md 去掉 frontmatter 后作为系统说明，再拼上本次输入。"""
    p = AGENTS_DIR / f"flow-{role}.md"
    if not p.exists():
        die(f"找不到角色定义 {p}")
    _, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return body.strip() + "\n\n---\n\n# 本次任务输入\n\n" + extra.strip() + "\n"


def external_cmd(engine, role, model, reasoning, root, out_file):
    """返回启动外部审查者 / 侦察者的命令行（prompt 走 stdin）。"""
    if engine == "codex":
        sandbox = "workspace-write" if role == "verifier" else "read-only"  # 审查要跑测试
        cmd = ["codex", "exec", "--ephemeral", "-s", sandbox, "-C", str(root), "-o", str(out_file)]
        if model:
            cmd += ["-m", model]
        if reasoning:
            cmd += ["-c", f'model_reasoning_effort="{reasoning}"']
        return cmd + ["-"]
    cmd = ["claude", "-p", "--agent", f"flow-{role}", "--allowedTools", "Read,Grep,Glob,Bash"]
    if model:
        cmd += ["--model", model]
    return cmd


def run_role(ctx, role, host, engine, model, reasoning, prompt, out_file, timeout):
    """同宿主的 Claude 走原生子 Agent（返回 native，由 skill 调起）；其余一律起外部进程。"""
    prompt_file = out_file.with_suffix(".prompt.md")
    prompt_file.write_text(prompt, encoding="utf-8")
    save_last(role, {"engine": engine, "model": model, "reasoning": reasoning})
    if engine == "claude" and host == "claude":
        return {"mode": "native", "prompt_file": str(prompt_file), "out_file": str(out_file)}
    if not shutil.which(engine):
        die(f"{engine} CLI 不可用，请换一个引擎重选")
    cmd = external_cmd(engine, role, model, reasoning, ctx.root, out_file)
    started = dt.datetime.now()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", cwd=ctx.root,
                            start_new_session=True)  # 独立进程组：超时时连同它启动的测试进程一起结束
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
        proc.communicate()
        die(f"{engine} 超过 {timeout}s 未返回，本轮作废")
    r = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    if engine == "claude":
        out_file.write_text(r.stdout, encoding="utf-8")
    if r.returncode != 0 and not (out_file.exists() and out_file.read_text(encoding="utf-8").strip()):
        die(f"{engine} 退出码 {r.returncode}：{(r.stderr or r.stdout).strip()[-800:]}")
    return {"mode": "external", "cmd": " ".join(cmd), "out_file": str(out_file),
            "seconds": int((dt.datetime.now() - started).total_seconds())}


def cmd_agent(ctx, a):
    """通用角色调度（目前给 scout 用）：输入文件 → 选定引擎/模型 → 输出文件。"""
    d = ctx.change(a.change)
    extra = "".join(Path(f).read_text(encoding="utf-8") + "\n" for f in a.input or [])
    if a.role == "scout":
        # scout 在 Codex 里是只读沙箱：召回结果由这里先写好，scout 只读 recalled.json
        cmd_recall(ctx, argparse.Namespace(text=extra, file=None, paths=None, top=None, json=False, record=True))
    extra += f"\n仓库根目录：{ctx.root}\n变更目录：{d}\n召回结果：{d / 'recalled.json'}\n"
    if a.role == "scout" and (ctx.ai / "kb" / "index.md").exists():
        extra += f"知识库索引：{ctx.ai / 'kb' / 'index.md'}（先读索引和下面召回的页，再查代码；页里的 file:line 仍要打开核对）\n"
        extra += "知识库召回：\n" + kb.recall_text(kb.recall_pages(ctx, extra, [], 5))
    out = (Path(a.out) if Path(a.out).is_absolute() else Path.cwd() / a.out) if a.out else d / f"{a.role}.out.md"
    res = run_role(ctx, a.role, a.host, a.engine, a.model, a.reasoning, role_prompt(a.role, extra),
                   out, a.timeout)
    trace(d, "proposal" if a.role == "scout" else "review", "note",
          note=f"{a.role} {a.engine}/{a.model} {res['mode']}")
    print(json.dumps(res, ensure_ascii=False, indent=2))


def tree_of(sha):
    return git("rev-parse", sha + "^{tree}").strip()


def acceptance_commands(d):
    p = d / "tasks.md"
    if not p.exists():
        return []
    return [m.group(1).strip() for m in re.finditer(r"验收[:：]\s*(.+)", p.read_text(encoding="utf-8"))
            if m.group(1).strip() and "<!--" not in m.group(1)]


def review_dir(d):
    rd = d / "review"
    rd.mkdir(exist_ok=True)
    return rd


def cmd_verify_start(ctx, a):
    d = ctx.change(a.change)
    snaps = load_snapshots(d)
    start = next((snaps[t]["base"] for t in snaps if snaps[t].get("base")), None)
    if not start:
        die("还没有任何任务的 base 快照，先完成 flow-build")
    rd = review_dir(d)
    n = len(list(rd.glob("round-*.json"))) + 1
    end = worktree_commit(f"flow {d.name} review round {n}")
    git("update-ref", f"refs/flow/{d.name}/review/{n}", end)
    level = independence(a.host, a.engine, a.model, a.host_model)
    meta = {"round": n, "host": a.host, "host_model": a.host_model, "engine": a.engine, "model": a.model,
            "reasoning": a.reasoning, "level": level, "start": start, "end": end,
            "tree_before": tree_of(end), "started_at": now()}
    lines = [f"- 仓库根目录：{ctx.root}", f"- 变更目录：{d}",
             f"- diff 起点：{start}", f"- diff 终点：{end}（已包含未提交和未跟踪的文件）",
             f"- 查看 diff：git diff {start} {end} -- . ':(exclude).ai'", "", "验收命令："]
    lines += [f"- `{c}`" for c in acceptance_commands(d)] or ["- （tasks.md 未写，按仓库常规编译 / 测试命令）"]
    prev = rd / f"round-{n - 1}.out.md"
    if n > 1 and prev.exists():
        lines += ["", "你上一轮的发现（逐条核对是否已修复）：", "", prev.read_text(encoding="utf-8")]
    out = rd / f"round-{n}.out.md"
    res = run_role(ctx, "verifier", a.host, a.engine, a.model, a.reasoning,
                   role_prompt("verifier", "\n".join(lines)), out, a.timeout)
    meta.update({"mode": res["mode"], "cmd": res.get("cmd"), "seconds": res.get("seconds")})
    (rd / f"round-{n}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if res["mode"] == "native":
        print(json.dumps({"round": n, "level": level, **res,
                          "next": "用 Agent 工具启动 flow-verifier 子 Agent（model 按用户选择），prompt 为 prompt_file 的全文；"
                                  "把它的完整回复写入 out_file，然后运行 flowctl verify record"},
                         ensure_ascii=False, indent=2))
        return
    record_round(ctx, d, n)


VERDICT_RE = re.compile(r"^[\s*]*结论[\s*]*[:：][\s*]*(PASS|REJECT)[\s*。.]*$", re.M)


def parse_verdict(text):
    """只认独占一行的「结论：PASS」或「结论：REJECT」，且全文只能有一种；否则视为无效。"""
    found = set(VERDICT_RE.findall(text))
    return found.pop() if len(found) == 1 else None


def worktree_changes(before_commit, after_commit):
    """审查前后的变化：返回 (被修改或删除的已有文件, 新增的文件)。"""
    out = git("diff", "--name-status", "--no-renames", before_commit, after_commit, "--", ".", *EXCLUDE)
    changed, added = [], []
    for line in out.splitlines():
        st, _, path = line.partition("\t")
        (added if st == "A" else changed).append(path)
    return changed, added


def record_round(ctx, d, n, force=False):
    rd = review_dir(d)
    mp = rd / f"round-{n}.json"
    meta = json.loads(mp.read_text(encoding="utf-8"))
    if meta.get("verdict") and not force:
        die(f"第 {n} 轮已记录为 {meta['verdict']}；需要重新审查请 verify start 开新一轮")
    out = rd / f"round-{n}.out.md"
    text = out.read_text(encoding="utf-8") if out.exists() else ""
    # 开审时的终点存在 git ref 里（审查者改不到 .ai/ 之外的它），以它为准比对工作区
    before = rev(f"refs/flow/{d.name}/review/{n}") or die(f"找不到第 {n} 轮的终点 ref")
    after = worktree_commit(f"flow {d.name} review round {n} check")
    changed, added = worktree_changes(before, after)
    verdict = parse_verdict(text)
    reason = ""
    if changed:
        verdict, reason = "INVALID", "审查者改动了已有文件：" + "、".join(changed[:5])
    elif not verdict:
        verdict, reason = "INVALID", "输出里没有唯一的一行「结论：PASS」或「结论：REJECT」"
    elif added:
        reason = "审查期间新增了未忽略的文件（多半是测试产物，请确认后删除或加入 .gitignore）：" + "、".join(added[:5])
    meta.update({"verdict": verdict, "invalid_reason": reason if verdict == "INVALID" else "",
                 "warning": reason if verdict != "INVALID" else "", "finished_at": now()})
    mp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if verdict in ("PASS", "REJECT"):
        trace(d, "review", "accepted" if verdict == "PASS" else "rejected",
              note=f"round {n} {meta['engine']}/{meta['model']} {meta['level']}")
    row = (f"| {n} | {verdict} | {meta['engine']} / {meta['model'] or '默认'}"
           f"{' / ' + meta['reasoning'] if meta.get('reasoning') else ''} | {meta['level']} "
           f"| [round-{n}.out.md](review/round-{n}.out.md) | {reason}{'（重新记录）' if force else ''} |")
    rv = d / "review.md"
    txt = rv.read_text(encoding="utf-8") if rv.exists() else ""
    marker = "<!-- flow:rounds -->"
    rv.write_text(txt.replace(marker, row + "\n" + marker) if marker in txt else txt + "\n" + row + "\n",
                  encoding="utf-8")
    print(f"第 {n} 轮：{verdict}（{meta['engine']}/{meta['model']}，独立级别 {meta['level']}）{reason}")
    if changed:
        print("工作区与开审时不一致：检查审查者改了什么（git status / git diff），恢复后重新审查。", file=sys.stderr)
    if verdict == "INVALID":
        sys.exit(2)


def cmd_verify_record(ctx, a):
    d = ctx.change(a.change)
    rd = review_dir(d)
    n = a.round or len(list(rd.glob("round-*.json")))
    if n < 1 or not (rd / f"round-{n}.json").exists():
        die("没有待记录的审查轮次，先运行 flowctl verify start")
    record_round(ctx, d, n, force=a.force)


# ---------------------------------------------------------------- 闭环：合入后追踪、定位、事故档案

def file_lines_at(ref, path):
    r = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, cwd=ROOT)
    if r.returncode != 0:
        return Counter()
    return Counter(s.strip() for s in r.stdout.decode("utf-8", "replace").splitlines() if s.strip())


def cmd_aftercare(ctx, a):
    d = ctx.change(a.change)
    mp = d / "metrics.json"
    if not mp.exists():
        die("先对这个变更跑 flowctl metrics（需在提交之后）")
    m = json.loads(mp.read_text(encoding="utf-8"))
    sig = m.get("ai_signature")
    if not sig:
        die("metrics.json 里没有 AI 行签名：请在变更提交后重新运行 flowctl metrics")
    at = a.at or "HEAD"
    at_sha = rev(at) or die(f"找不到 {at}")
    by_file = {}
    for path, line, n in sig:
        by_file.setdefault(path, []).append((line, n))
    total = alive = 0
    for path, items in by_file.items():
        cur = file_lines_at(at_sha, path)
        for line, n in items:
            total += n
            alive += min(n, cur[line])
    closed = next((r["ts"] for r in reversed(read_jsonl(d / "trace.jsonl")) if r["event"] == "closed"),
                  m.get("computed_at"))
    own = set(m.get("commits", []))
    follow = []
    if m.get("files"):
        out = git("log", "--format=%H%x09%ad%x09%s", "--date=short", f"--since={closed}", at_sha, "--",
                  *m["files"])
        follow = [l.split("\t", 2) for l in out.splitlines() if l and l.split("\t")[0] not in own]
    res = {"change": d.name, "at": at, "at_sha": at_sha, "checked_at": now(), "since": closed,
           "ai_lines": total, "ai_alive": alive, "survival": (alive / total) if total else None,
           "followup_commits": [{"sha": s, "date": dte, "subject": subj} for s, dte, subj in follow]}
    hp = d / "aftercare.json"
    hist = json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else []
    hist.append(res)
    hp.write_text(json.dumps(hist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"变更 {d.name}（对照 {at}）")
    print(f"AI 代码存活率：{alive}/{total} = {pct(res['survival']).strip()}")
    print(f"关闭以来改动过这批文件的提交：{len(follow)} 个")
    for s, dte, subj in follow[:10]:
        print(f"  {s[:10]} {dte} {subj}")


def cmd_locate(ctx, a):
    ctx.need_init()
    path, line = a.target, None
    m = re.match(r"^(.*):(\d+)$", a.target)
    if m:
        path, line = m.group(1), int(m.group(2))
    path = os.path.relpath(os.path.abspath(path), ctx.root)  # 允许从子目录按相对路径查
    sha = None
    if line:
        out = git("blame", "--porcelain", "-L", f"{line},{line}", "HEAD", "--", path, check=False)
        sha = out.split(" ", 1)[0] if out else None
        if sha:
            print(f"{path}:{line} 最后由 {sha[:10]} 修改：{git('log', '-1', '--format=%ad %s', '--date=short', sha).strip()}")
    hits = []
    for d in sorted((ctx.ai / "changes").iterdir()):
        mp = d / "metrics.json"
        if not mp.exists():
            continue
        mm = json.loads(mp.read_text(encoding="utf-8"))
        if sha and sha in mm.get("commits", []):
            hits.append((d, "引入这一行的提交属于该变更"))
        elif path in mm.get("files", []):
            hits.append((d, "该变更改动过这个文件"))
    if not hits:
        print("没有找到相关变更档案（可能来自 Vibe 通道或流程之外的提交）")
    for d, why in sorted(hits, key=lambda x: x[1] != "引入这一行的提交属于该变更"):
        title = ""
        p = d / "proposal.md"
        if p.exists():
            title = p.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
        print(f"- {d.relative_to(ctx.root)}  {title}  ← {why}")


def cmd_incident_new(ctx, a):
    ctx.need_init()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,48}", a.slug):
        die("slug 只能用小写字母、数字和连字符，2–49 个字符")
    idir = ctx.ai / "incidents"
    idir.mkdir(exist_ok=True)
    p = idir / f"{dt.date.today():%Y%m%d}-{a.slug}.md"
    if p.exists():
        die(f"已存在：{p.name}")
    tpl = (TEMPLATE_DIR / "incident.md").read_text(encoding="utf-8")
    p.write_text(tpl.replace("{{title}}", a.title or a.slug).replace("{{date}}", str(dt.date.today())),
                 encoding="utf-8")
    print(f"已创建 {p.relative_to(ctx.root)}")


# ---------------------------------------------------------------- 入口

def main():
    p = argparse.ArgumentParser(prog="flowctl", description="个人 AI Coding 流程工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="在当前仓库初始化 .ai/")
    s.add_argument("--local-only", action="store_true", help="把 .ai/ 加入 .git/info/exclude，只在本地保留")
    s = sub.add_parser("new", help="新建一次变更")
    s.add_argument("slug")
    s.add_argument("--title")
    s = sub.add_parser("use", help="切换当前变更")
    s.add_argument("change")
    sub.add_parser("list", help="列出变更")
    for name, h in (("status", "查看变更进度与下一步"), ("close", "关闭变更")):
        s = sub.add_parser(name, help=h)
        s.add_argument("change", nargs="?")

    s = sub.add_parser("trace", help="记录过程事件")
    s.add_argument("stage", choices=STAGES)
    s.add_argument("event", choices=EVENTS)
    s.add_argument("--task")
    s.add_argument("--note")
    s.add_argument("--change")

    s = sub.add_parser("recall", help="按需求文本 / 涉及路径召回 spec")
    s.add_argument("--text")
    s.add_argument("--file", action="append")
    s.add_argument("--paths", nargs="*")
    s.add_argument("--top", type=int)
    s.add_argument("--json", action="store_true")
    s.add_argument("--record", action="store_true", help="写入当前变更的 recalled.json")

    s = sub.add_parser("score", help="给一条 spec 打分")
    s.add_argument("spec")
    s.add_argument("verdict", choices=VERDICTS)
    s.add_argument("--note")

    s = sub.add_parser("specs", help="spec 库维护")
    s.add_argument("action", choices=("lint", "report"))

    s = sub.add_parser("kb", help="代码知识库：scan / plan / draft / facts / lint / freeze / status / recall")
    s.add_argument("action", choices=("scan", "plan", "draft", "facts", "lint", "freeze", "status", "recall"))
    s.add_argument("--page", help="draft / facts：页 id")
    s.add_argument("--all", action="store_true", help="draft：处理 plan 里所有待写的页")
    s.add_argument("--force", action="store_true", help="draft：覆盖已有页（protected 页除外，kb:manual 块保留）")
    s.add_argument("--text", help="recall：需求文本")
    s.add_argument("--file", action="append", help="recall：需求文件")
    s.add_argument("--paths", nargs="*", help="recall：涉及路径")
    s.add_argument("--top", type=int, help="recall：最多返回几页，默认 5")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("snapshot", help="给任务打快照：base（开工前）、v1（AI 首版）、final（任务验收）")
    s.add_argument("task")
    s.add_argument("phase", choices=PHASES)
    s.add_argument("--change")

    s = sub.add_parser("checkpoint", help="把工作区存成审查用的 diff 终点，并打印起点")
    s.add_argument("--change")

    s = sub.add_parser("metrics", help="计算当前变更的 FPY、AI 代码占比、采纳率")
    s.add_argument("change", nargs="?")
    s.add_argument("--to", help="AI 代码占比的终点提交，默认 HEAD")
    s.add_argument("--squash", action="store_true", help="--to 是 squash 合入的提交：只统计这一个提交本身")
    s.add_argument("--json", action="store_true")

    sub.add_parser("report", help="汇总所有变更的度量")

    s = sub.add_parser("models", help="列出某个角色可选的引擎与模型（供 skill 让用户选）")
    s.add_argument("role", choices=ROLES)
    s.add_argument("--json", action="store_true")

    def role_args(s):
        s.add_argument("--host", required=True, choices=HOSTS, help="当前在哪个工具里运行")
        s.add_argument("--engine", required=True, choices=HOSTS, help="用户选择的引擎")
        s.add_argument("--model", help="用户选择的模型；不填用该引擎默认模型")
        s.add_argument("--reasoning", help="推理档位（仅 codex）")
        s.add_argument("--timeout", type=int, default=1800)
        s.add_argument("--change")

    s = sub.add_parser("agent", help="按用户选择的引擎/模型运行一个角色（scout）")
    s.add_argument("role", choices=ROLES)
    s.add_argument("--input", action="append", help="作为输入拼进 prompt 的文件")
    s.add_argument("--out")
    role_args(s)

    s = sub.add_parser("verify", help="独立审查：start 开一轮，record 记录原生子 Agent 的结果")
    vs = s.add_subparsers(dest="vcmd", required=True)
    s1 = vs.add_parser("start")
    role_args(s1)
    s1.add_argument("--host-model", help="当前会话（实现者）用的模型，用来判定独立级别")
    s2 = vs.add_parser("record")
    s2.add_argument("--round", type=int)
    s2.add_argument("--force", action="store_true", help="覆盖已记录的结论（会在表中注明）")
    s2.add_argument("--change")

    s = sub.add_parser("aftercare", help="合入后追踪：AI 代码存活率、后续改动")
    s.add_argument("change", nargs="?")
    s.add_argument("--at", help="对照的提交，默认 HEAD")

    s = sub.add_parser("locate", help="从 <文件>[:行] 追到来源变更档案")
    s.add_argument("target")

    s = sub.add_parser("incident", help="线上问题档案")
    s.add_argument("action", choices=("new",))
    s.add_argument("slug")
    s.add_argument("--title")

    a = p.parse_args()
    ctx = None if a.cmd == "models" else Ctx()  # 列模型不需要在仓库里
    handlers = {
        "init": cmd_init, "new": cmd_new, "use": cmd_use, "list": cmd_list, "status": cmd_status,
        "close": cmd_close, "trace": cmd_trace, "recall": cmd_recall, "score": cmd_score,
        "snapshot": cmd_snapshot, "checkpoint": cmd_checkpoint, "metrics": cmd_metrics, "report": cmd_report,
        "models": cmd_models, "agent": cmd_agent, "aftercare": cmd_aftercare, "locate": cmd_locate,
        "incident": cmd_incident_new,
        "verify": lambda c, x: (cmd_verify_start if x.vcmd == "start" else cmd_verify_record)(c, x),
        "specs": lambda c, x: (cmd_specs_lint if x.action == "lint" else cmd_specs_report)(c, x),
        "kb": kb.dispatch,
    }
    kb.bind(sys.modules[__name__])
    handlers[a.cmd](ctx, a)


if __name__ == "__main__":
    main()
