"""kb：仓库代码知识库（.ai/kb/）的扫描、规划、草稿、校验、冻结、状态与召回。

只依赖 Python 3.9+ 标准库和 git。事实抽取覆盖 Java / Go / Python，其他语言只统计文件与 manifest。
所有抽取函数都是纯函数（文本 → 事实列表），便于直接单测。

  .ai/kb/scan.json        kb scan 的事实（每条带 file:line）
  .ai/kb/plan.json        页面规划（用户可编辑）
  .ai/kb/ai-quick-reference.md、architecture/*.md、modules/*.md、domains/*/*.md   知识库页（frontmatter + 正文）
  .ai/kb/README.md 及各层 README.md   导航（kb freeze 生成）
  .ai/kb/_manifest.json   冻结基线（commit、每页 sources 与内容 hash）

由 flowctl.py 通过 bind() 注入 git / die / glob_match / trigger_hit / parse_frontmatter 等公共函数。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

F = None  # flowctl 模块，由 bind() 注入


def bind(mod):
    global F
    F = mod


# ---------------------------------------------------------------- 常量

LANG_EXT = {".java": "java", ".kt": "java", ".go": "go", ".py": "python"}
SKIP_SEGS = {"vendor", "node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__",
             ".idea", ".ai", "generated", ".git"}
MAX_SIZE = 1024 * 1024
LAYER_WORDS = {"controller", "web", "api", "service", "biz", "repository", "dao", "mapper", "model",
               "entity", "domain", "dto", "vo", "config", "util", "common", "infra"}
REAL_ENTRY_KINDS = {"http", "rpc", "mq", "schedule", "event", "cli", "external-callback"}  # main 不算对外入口
TODO = "<!-- kb:todo -->"
MANUAL_OPEN, MANUAL_CLOSE = "<!-- kb:manual -->", "<!-- /kb:manual -->"
CONFIG_PATTERNS = ["**/application*.yml", "**/application*.yaml", "**/application*.properties",
                   "**/bootstrap*.yml", "**/bootstrap*.yaml", "config/*.yaml", "config/*.yml", "config/*.toml",
                   ".env*", "**/.env*", "**/settings.py", "**/settings/*.py", "**/logback*.xml", "alembic.ini"]
KIND_LABEL = {"http": "证据-接口", "rpc": "证据-接口", "mq": "证据-起点", "schedule": "证据-起点", "event": "证据-起点",
              "cli": "证据-起点", "main": "证据-起点", "external-callback": "证据-起点"}
REF_RX = re.compile(r"(?<![\w/:.])((?:[\w.\-]+/)*[\w.\-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?\b")


def kb_dir(ctx):
    return ctx.ai / "kb"


def sha1(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def read_text(p):
    return Path(p).read_text(encoding="utf-8", errors="replace")


def dump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(p, default=None):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def head_sha():
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=F.ROOT, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


# ---------------------------------------------------------------- 文件枚举

def enumerate_files(ctx, scope=None):
    """已跟踪文件，去掉禁区目录、超大文件、二进制文件，再按 scope include/exclude 过滤。"""
    files, skipped = [], Counter()
    raw = F.git("ls-files", "-z").split("\0")
    inc = (scope or {}).get("include") or []
    exc = (scope or {}).get("exclude") or []
    for rel in raw:
        if not rel:
            continue
        segs = rel.split("/")
        if any(s in SKIP_SEGS for s in segs):
            skipped["excluded_dir"] += 1
            continue
        if exc and any(F.glob_match(rel, g) for g in exc):
            skipped["scope"] += 1
            continue
        if inc and not any(F.glob_match(rel, g) for g in inc):
            skipped["scope"] += 1
            continue
        p = ctx.root / rel
        if not p.is_file():
            skipped["missing"] += 1
            continue
        if p.stat().st_size > MAX_SIZE:
            skipped["large"] += 1
            continue
        with open(p, "rb") as fh:
            if b"\0" in fh.read(8192):
                skipped["binary"] += 1
                continue
        files.append(rel)
    return files, skipped


def lang_of(rel):
    return LANG_EXT.get(Path(rel).suffix.lower())


def is_test_file(rel):
    name = Path(rel).name
    return ("/src/test/" in "/" + rel or rel.startswith("src/test/") or name.endswith("_test.go")
            or name.startswith("test_") and name.endswith(".py") or name.endswith("_test.py")
            or rel.startswith("tests/") or "/tests/" in rel or name == "conftest.py"
            or name.endswith("Test.java") or name.endswith("Tests.java"))


def _line_of(text, needle, start=0):
    i = text.find(needle, start)
    return text.count("\n", 0, i) + 1 if i >= 0 else 1


def _join_paren(lines, i, limit=8):
    """从第 i 行起把未闭合的括号拼到一起（最多 limit 行）。"""
    buf = lines[i]
    j = i
    while buf.count("(") > buf.count(")") and j + 1 < len(lines) and j - i < limit:
        j += 1
        buf += " " + lines[j].strip()
    return buf


# ---------------------------------------------------------------- 构建与依赖

def parse_pom(rel, text):
    """Maven pom：模块、parent、properties、依赖（解析 ${prop}）。"""
    out = {"file": rel, "line": 1, "ecosystem": "maven", "name": None, "version": None, "frameworks": {},
           "modules": [], "dependencies": [], "properties": {}, "plugins": []}
    try:
        root = ET.fromstring(text.encode("utf-8", "replace"))
    except ET.ParseError:
        out["modules"] = re.findall(r"<module>([^<]+)</module>", text)
        out["name"] = (re.search(r"<artifactId>([^<]+)</artifactId>", text) or [None, None])[1]
        return out

    def tag(e):
        return e.tag.rsplit("}", 1)[-1]

    def child(e, name):
        for c in e:
            if tag(c) == name:
                return c
        return None

    def txt(e, name):
        c = child(e, name) if e is not None else None
        return (c.text or "").strip() if c is not None and c.text else None

    props = {}
    pe = child(root, "properties")
    if pe is not None:
        for c in pe:
            props[tag(c)] = (c.text or "").strip()
    out["name"] = txt(root, "artifactId")
    parent = child(root, "parent")
    version = txt(root, "version") or (txt(parent, "version") if parent is not None else None)
    out["version"] = version
    if version:
        props.setdefault("project.version", version)
    if parent is not None and txt(parent, "artifactId") == "spring-boot-starter-parent":
        out["frameworks"]["spring_boot"] = txt(parent, "version")
    out["properties"] = props
    me = child(root, "modules")
    if me is not None:
        out["modules"] = [(c.text or "").strip() for c in me if (c.text or "").strip()]

    def resolve(v):
        if not v:
            return v, True
        ok = True

        def rep(m):
            nonlocal ok
            if m.group(1) in props:
                return props[m.group(1)]
            ok = False
            return m.group(0)
        return re.sub(r"\$\{([^}]+)\}", rep, v), ok

    for holder in (child(root, "dependencies"), child(child(root, "dependencyManagement") or root, "dependencies")
                   if child(root, "dependencyManagement") is not None else None):
        if holder is None:
            continue
        for d in holder:
            if tag(d) != "dependency":
                continue
            art = txt(d, "artifactId")
            ver, ok = resolve(txt(d, "version"))
            out["dependencies"].append({"group": txt(d, "groupId"), "artifact": art, "version": ver,
                                        "resolved": ok, "line": _line_of(text, f"<artifactId>{art}</artifactId>")})
            if art == "spring-boot-dependencies" and ver:
                out["frameworks"].setdefault("spring_boot", ver)
    build = child(root, "build")
    plugins = child(build, "plugins") if build is not None else None
    if plugins is not None:
        out["plugins"] = [txt(p, "artifactId") for p in plugins if tag(p) == "plugin" and txt(p, "artifactId")]
    return out


GRADLE_DEP_RX = re.compile(r"""^\s*(implementation|api|compileOnly|runtimeOnly|testImplementation|annotationProcessor|kapt)\s*\(?\s*["']([^"':]+):([^"':]+)(?::([^"']+))?["']""", re.M)


def parse_gradle(rel, text):
    out = {"file": rel, "line": 1, "ecosystem": "gradle", "name": None, "version": None, "frameworks": {},
           "modules": [], "dependencies": [], "plugins": []}
    for m in GRADLE_DEP_RX.finditer(text):
        out["dependencies"].append({"group": m.group(2), "artifact": m.group(3), "version": m.group(4),
                                    "resolved": m.group(4) is not None and "$" not in (m.group(4) or ""),
                                    "scope": m.group(1), "line": text.count("\n", 0, m.start()) + 1})
    m = re.search(r"""["']org\.springframework\.boot["']\)?\s*version\s*["']([^"']+)""", text)
    if m:
        out["frameworks"]["spring_boot"] = m.group(1)
    for p in ("spotless", "checkstyle", "ktlint"):
        if p in text:
            out["plugins"].append(p)
    return out


def parse_gradle_settings(text):
    mods = []
    for m in re.finditer(r"""include\s*\(?\s*((?:["']:?[\w:\-]+["']\s*,?\s*)+)\)?""", text):
        mods += [x.strip(":").replace(":", "/") for x in re.findall(r"""["']([^"']+)["']""", m.group(1))]
    return mods


def parse_gomod(rel, text):
    out = {"file": rel, "line": 1, "ecosystem": "go", "name": None, "go": None, "frameworks": {}, "modules": [],
           "dependencies": []}
    m = re.search(r"^module\s+(\S+)", text, re.M)
    out["name"] = m.group(1) if m else None
    m = re.search(r"^go\s+(\d+\.\d+(?:\.\d+)?)", text, re.M)
    out["go"] = m.group(1) if m else None
    lines = text.splitlines()
    in_block = False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith("require ("):
            in_block = True
            continue
        if in_block and s == ")":
            in_block = False
            continue
        m = re.match(r"^require\s+(\S+)\s+(\S+)", s) if not in_block else re.match(r"^(\S+)\s+(v\S+)", s)
        if m and not s.startswith("//"):
            out["dependencies"].append({"artifact": m.group(1), "version": m.group(2), "resolved": True,
                                        "indirect": "// indirect" in s, "line": i})
    return out


def parse_pyproject(rel, text):
    out = {"file": rel, "line": 1, "ecosystem": "python", "name": None, "version": None, "frameworks": {},
           "modules": [], "dependencies": [], "tools": []}
    section, in_deps = None, False
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        m = re.match(r"^\[([^\]]+)\]", s)
        if m:
            section, in_deps = m.group(1), False
            for t in ("tool.pytest", "tool.ruff", "tool.black", "tool.flake8", "build-system", "tool.poetry"):
                if section.startswith(t) and t not in out["tools"]:
                    out["tools"].append(t)
            continue
        if section == "project":
            m = re.match(r"""^name\s*=\s*["']([^"']+)""", s)
            if m:
                out["name"] = m.group(1)
            m = re.match(r"""^version\s*=\s*["']([^"']+)""", s)
            if m:
                out["version"] = m.group(1)
            if s.startswith("dependencies"):
                in_deps = True
            if in_deps:
                for d in re.findall(r"""["']([^"']+)["']""", s):
                    out["dependencies"].append(_pydep(d, i))
                if s.endswith("]"):
                    in_deps = False
        elif section in ("tool.poetry.dependencies", "tool.poetry.dev-dependencies", "tool.poetry.group.dev.dependencies"):
            m = re.match(r"""^([\w\-]+)\s*=\s*(.+)$""", s)
            if m and m.group(1) != "python":
                ver = re.search(r"""["']([^"']+)["']""", m.group(2))
                out["dependencies"].append({"artifact": m.group(1), "version": ver.group(1) if ver else None,
                                            "resolved": True, "line": i})
    return out


def _pydep(spec, line):
    m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(.*)$", spec.strip())
    return {"artifact": m.group(1) if m else spec, "version": (m.group(2).strip() or None) if m else None,
            "resolved": True, "line": line}


def parse_requirements(rel, text):
    out = {"file": rel, "line": 1, "ecosystem": "python", "name": None, "frameworks": {}, "modules": [],
           "dependencies": [], "tools": []}
    for i, line in enumerate(text.splitlines(), 1):
        s = line.split("#")[0].strip()
        if not s or s.startswith("-"):
            continue
        out["dependencies"].append(_pydep(s, i))
    return out


def parse_setup(rel, text):
    out = {"file": rel, "line": 1, "ecosystem": "python", "name": None, "frameworks": {}, "modules": [],
           "dependencies": [], "tools": []}
    m = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.S) or re.search(r"install_requires\s*=\s*\n((?:\s+.+\n)+)", text)
    if m:
        for d in re.findall(r"""["']([^"']+)["']""", m.group(1)) or [x.strip() for x in m.group(1).splitlines() if x.strip()]:
            out["dependencies"].append(_pydep(d, _line_of(text, d)))
    m = re.search(r"""name\s*=\s*["']([^"']+)""", text)
    out["name"] = m.group(1) if m else None
    return out


def parse_generic_manifest(rel, text):
    eco = "node" if rel.endswith("package.json") else "rust"
    out = {"file": rel, "line": 1, "ecosystem": eco, "name": None, "frameworks": {}, "modules": [], "dependencies": [],
           "scripts": {}}
    if eco == "node":
        try:
            pj = json.loads(text)
            out["name"] = pj.get("name")
            out["scripts"] = {k: v for k, v in (pj.get("scripts") or {}).items() if k in ("build", "test", "lint")}
        except json.JSONDecodeError:
            pass
    else:
        m = re.search(r"""^name\s*=\s*["']([^"']+)""", text, re.M)
        out["name"] = m.group(1) if m else None
    return out


def detect_build(ctx, files):
    """找 manifest，解析，推导命令。"""
    manifests, texts = [], {}
    for rel in files:
        name = Path(rel).name
        low = name.lower()
        if name == "pom.xml":
            manifests.append(parse_pom(rel, read_text(ctx.root / rel)))
        elif name in ("build.gradle", "build.gradle.kts"):
            manifests.append(parse_gradle(rel, read_text(ctx.root / rel)))
        elif name in ("settings.gradle", "settings.gradle.kts"):
            texts[rel] = read_text(ctx.root / rel)
        elif name == "go.mod":
            manifests.append(parse_gomod(rel, read_text(ctx.root / rel)))
        elif name == "pyproject.toml":
            manifests.append(parse_pyproject(rel, read_text(ctx.root / rel)))
        elif re.match(r"^requirements[\w.\-]*\.txt$", low):
            manifests.append(parse_requirements(rel, read_text(ctx.root / rel)))
        elif name in ("setup.py", "setup.cfg"):
            manifests.append(parse_setup(rel, read_text(ctx.root / rel)))
        elif name in ("package.json", "Cargo.toml") and "/" not in rel:
            manifests.append(parse_generic_manifest(rel, read_text(ctx.root / rel)))
    for rel, t in texts.items():
        mods = parse_gradle_settings(t)
        base = str(Path(rel).parent) if "/" in rel else ""
        for m in manifests:
            if m["ecosystem"] == "gradle" and str(Path(m["file"]).parent).rstrip(".") == base.rstrip("."):
                m["modules"] = mods
    # 根 manifest 优先（路径最短）
    manifests.sort(key=lambda m: (m["file"].count("/"), m["file"]))
    ecosystems = []
    for m in manifests:
        if m["ecosystem"] not in ecosystems:
            ecosystems.append(m["ecosystem"])
    return {"ecosystems": ecosystems, "manifests": manifests,
            "commands": derive_commands(ctx, files, manifests, ecosystems)}


def derive_commands(ctx, files, manifests, ecosystems):
    fs = set(files)
    cmd = {"build": None, "test": None, "test_one": None, "lint": None}

    def g(c, why):
        return {"cmd": c, "guessed": True, "why": why}

    if not ecosystems:
        return cmd
    eco = ecosystems[0]
    root = [m for m in manifests if m["ecosystem"] == eco][0]
    if eco == "maven":
        mvn = "./mvnw" if "mvnw" in fs else "mvn"
        why = "检测到 ./mvnw" if mvn == "./mvnw" else "检测到 pom.xml，未见 wrapper"
        cmd["build"] = g(f"{mvn} -q -DskipTests package", why)
        cmd["test"] = g(f"{mvn} test", why)
        cmd["test_one"] = g(f"{mvn} -pl <模块> test" if root["modules"] else f"{mvn} -Dtest=<测试类> test", why)
        plugins = set(root.get("plugins") or [])
        if any("checkstyle" in p for p in plugins):
            cmd["lint"] = g(f"{mvn} checkstyle:check", "pom 里有 checkstyle 插件")
        elif any("spotless" in p for p in plugins):
            cmd["lint"] = g(f"{mvn} spotless:check", "pom 里有 spotless 插件")
    elif eco == "gradle":
        gw = "./gradlew" if "gradlew" in fs else "gradle"
        why = "检测到 ./gradlew" if gw == "./gradlew" else "检测到 build.gradle，未见 wrapper"
        cmd["build"] = g(f"{gw} build -x test", why)
        cmd["test"] = g(f"{gw} test", why)
        cmd["test_one"] = g(f"{gw} :<模块>:test" if root["modules"] else f"{gw} test --tests <测试类>", why)
        if root.get("plugins"):
            cmd["lint"] = g(f"{gw} check", f"build.gradle 里有 {'/'.join(root['plugins'])}")
    elif eco == "go":
        cmd["build"] = g("go build ./...", "检测到 go.mod")
        cmd["test"] = g("go test ./...", "检测到 go.mod")
        cmd["test_one"] = g("go test ./<目录>/...", "检测到 go.mod")
        if any(re.match(r"^\.golangci\.ya?ml$", f) for f in fs):
            cmd["lint"] = g("golangci-lint run", "检测到 .golangci.yml")
        else:
            cmd["lint"] = g("go vet ./...", "未见 golangci 配置，退回 go vet")
    elif eco == "python":
        pys = [m for m in manifests if m["ecosystem"] == "python"]
        tools = {t for m in pys for t in m.get("tools", [])}
        deps = {d["artifact"].lower() for m in pys for d in m["dependencies"]}
        pytest = ("tool.pytest" in tools or "pytest.ini" in fs or "tox.ini" in fs or "conftest.py" in fs
                  or any(f.endswith("/conftest.py") for f in fs) or "pytest" in deps)
        if pytest:
            cmd["test"] = g("python -m pytest -q", "检测到 pytest 配置或依赖")
            cmd["test_one"] = g("python -m pytest -q <目录或文件>", "检测到 pytest 配置或依赖")
        else:
            cmd["test"] = g("python -m unittest discover -s tests", "未见 pytest 迹象，退回 unittest")
            cmd["test_one"] = g("python -m unittest <模块路径>", "未见 pytest 迹象，退回 unittest")
        if "tool.ruff" in tools or "ruff.toml" in fs or "ruff" in deps:
            cmd["lint"] = g("ruff check .", "检测到 ruff 配置")
        elif "tool.flake8" in tools or ".flake8" in fs or "flake8" in deps:
            cmd["lint"] = g("flake8", "检测到 flake8 配置")
        elif "tool.black" in tools or "black" in deps:
            cmd["lint"] = g("black --check .", "检测到 black 配置")
        if "tool.poetry" in tools:
            cmd["build"] = g("poetry build", "检测到 [tool.poetry]")
        elif "build-system" in tools:
            cmd["build"] = g("python -m build", "检测到 [build-system]")
    elif eco == "node":
        for k in ("build", "test", "lint"):
            if root.get("scripts", {}).get(k):
                cmd[k] = g(f"npm run {k}", "package.json scripts")
    return cmd


# ---------------------------------------------------------------- 模块划分

def java_package(text):
    m = re.search(r"^\s*package\s+([\w.]+)\s*;?", text, re.M)
    return m.group(1) if m else None


def common_prefix(pkgs):
    if not pkgs:
        return []
    parts = [p.split(".") for p in pkgs]
    pre = parts[0]
    for p in parts[1:]:
        i = 0
        while i < min(len(pre), len(p)) and pre[i] == p[i]:
            i += 1
        pre = pre[:i]
    return pre


def cluster_modules(ctx, files, build, texts):
    """返回 (modules dict id->module, file->module id)。测试文件也分配模块，但不计入 files。"""
    by_lang = defaultdict(list)
    for rel in files:
        lg = lang_of(rel)
        if lg:
            by_lang[lg].append(rel)
    modules, fmod = {}, {}

    def put(mid, rel, lg, package=None, layout=None):
        m = modules.setdefault(mid, {"id": mid, "name": mid.split("/")[-1].split(".")[-1], "lang": lg, "dirs": set(),
                                     "packages": set(), "files": 0, "loc": 0, "tests": 0, "layers": Counter(),
                                     "layout": layout or "flat", "entries": 0, "models": 0})
        fmod[rel] = mid
        m["dirs"].add(str(Path(rel).parent) if "/" in rel else ".")
        if package:
            m["packages"].add(package)
        for seg in rel.lower().split("/")[:-1]:
            if seg in LAYER_WORDS:
                m["layers"][seg] += 1
        if is_test_file(rel):
            m["tests"] += 1
        else:
            m["files"] += 1
            m["loc"] += texts[rel].count("\n") + 1

    # Java
    if by_lang["java"]:
        mvn_mods = []
        for man in build["manifests"]:
            if man["ecosystem"] in ("maven", "gradle") and man["modules"]:
                base = str(Path(man["file"]).parent)
                base = "" if base == "." else base + "/"
                mvn_mods += [base + x.strip("/") for x in man["modules"]]
        pkgs = {rel: java_package(texts[rel]) for rel in by_lang["java"]}
        if mvn_mods:
            mvn_mods.sort(key=len, reverse=True)
            for rel in by_lang["java"]:
                mid = next((m for m in mvn_mods if rel.startswith(m + "/")), "root")
                put(mid, rel, "java", pkgs[rel], "multi-module")
        else:
            src_pkgs = [pkgs[r] for r in by_lang["java"] if pkgs[r] and not is_test_file(r)]
            base = common_prefix(src_pkgs)
            if len(base) < 2 and src_pkgs:
                base = base[:1]
            nxt = Counter()
            for p in src_pkgs:
                segs = p.split(".")
                if len(segs) > len(base):
                    nxt[segs[len(base)]] += 1
            layered = nxt and sum(v for k, v in nxt.items() if k in LAYER_WORDS) / max(1, sum(nxt.values())) >= 0.6
            for rel in by_lang["java"]:
                p = pkgs[rel]
                segs = p.split(".") if p else []
                rest = segs[len(base):] if p and segs[:len(base)] == base else segs
                if layered:
                    rest = [s for s in rest if s not in LAYER_WORDS]
                mid = rest[0] if rest else "common"
                put(mid, rel, "java", p, "layered" if layered else "package")
    # Go
    gomod = next((m for m in build["manifests"] if m["ecosystem"] == "go"), None)
    for rel in by_lang["go"]:
        segs = rel.split("/")
        if len(segs) > 2 and segs[0] in ("cmd", "internal", "pkg"):
            mid = segs[0] + "/" + segs[1]
        elif len(segs) > 1:
            mid = segs[0]
        else:
            mid = "root"
        pkg = (gomod["name"] + "/" + str(Path(rel).parent)).rstrip("/.") if gomod and gomod["name"] else None
        put(mid, rel, "go", pkg, "dir")
    # Python
    if by_lang["python"]:
        fs = set(files)
        roots = {}
        for rel in by_lang["python"]:
            for base in ("", "src/"):
                segs = rel[len(base):].split("/") if rel.startswith(base) else None
                if segs and len(segs) > 1 and f"{base}{segs[0]}/__init__.py" in fs and segs[0] != "tests":
                    roots[f"{base}{segs[0]}"] = segs[0]
        split_deep = False
        if len(roots) < 3:
            for r in roots:
                subs = {rel[len(r) + 1:].split("/")[0] for rel in by_lang["python"]
                        if rel.startswith(r + "/") and rel[len(r) + 1:].count("/") >= 1 and f"{r}/{rel[len(r) + 1:].split('/')[0]}/__init__.py" in fs}
                if subs:
                    split_deep = True
        for rel in by_lang["python"]:
            root = next((r for r in sorted(roots, key=len, reverse=True) if rel.startswith(r + "/")), None)
            if root is None:
                mid = "tests" if is_test_file(rel) else "root"
                if mid == "tests":
                    fmod[rel] = None
                    continue
                put(mid, rel, "python", None, "dir")
                continue
            name = roots[root]
            inner = rel[len(root) + 1:].split("/")
            if split_deep and len(inner) > 1 and f"{root}/{inner[0]}/__init__.py" in fs:
                mid, pkg = f"{name}.{inner[0]}", f"{name}.{inner[0]}"
            else:
                mid, pkg = name, name
            put(mid, rel, "python", pkg, "package")
    # 分级与合并
    n_src = sum(1 for r in files if lang_of(r) and not is_test_file(r))
    tier = "flat" if n_src <= 150 else ("modular" if n_src <= 1500 else "large")
    cap = {"flat": 6, "modular": 15, "large": 10 ** 9}[tier]
    notes = []
    if tier == "large":
        notes.append("large：按顶层目录分组，深层模块需人工在 plan.json 里拆分")
    while len([m for m in modules.values() if m["files"] > 0]) > cap:
        smallest = min((m for m in modules.values() if m["id"] != "common" and m["files"] > 0), key=lambda m: m["files"])
        tgt = modules.setdefault("common", {"id": "common", "name": "common", "lang": smallest["lang"], "dirs": set(),
                                            "packages": set(), "files": 0, "loc": 0, "tests": 0, "layers": Counter(),
                                            "layout": "merged", "entries": 0, "models": 0, "merged_from": []})
        for k in ("files", "loc", "tests"):
            tgt[k] += smallest[k]
        tgt["dirs"] |= smallest["dirs"]
        tgt["packages"] |= smallest["packages"]
        tgt["layers"].update(smallest["layers"])
        tgt.setdefault("merged_from", []).append(smallest["id"])
        for rel, mid in list(fmod.items()):
            if mid == smallest["id"]:
                fmod[rel] = "common"
        del modules[smallest["id"]]
    for m in modules.values():
        m["dirs"] = sorted(m["dirs"])
        m["packages"] = sorted(m["packages"])
        m["layers"] = dict(m["layers"])
    return modules, fmod, tier, notes


# ---------------------------------------------------------------- 事实抽取（纯函数）

def _handler_after(lines, i, limit=6):
    for j in range(i + 1, min(len(lines), i + 1 + limit)):
        s = lines[j].strip()
        if not s or s.startswith("@") or s.startswith("//") or s.startswith("*"):
            continue
        if re.search(r"\b(class|interface|enum|record)\s+\w+", s):
            return None
        m = re.search(r"[\w<>\[\], ?.]+\s+(\w+)\s*\(", s)
        if m:
            return m.group(1)
    return None


def _first_str(s):
    m = re.search(r'"([^"]*)"', s or "")
    return m.group(1) if m else ""


def java_entries(rel, text):
    lines = text.splitlines()
    out, prefix, is_ctrl = [], "", False
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("@") and re.search(r"\b(class|interface)\s+\w+", s):
            # 新类型开始：类级前缀在下一处 @RequestMapping 之前保持不变
            pass
        if re.match(r"@(RestController|Controller)\b", s):
            is_ctrl, prefix = True, ""
            continue
        m = re.match(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b", s)
        if m:
            ann = _join_paren(lines, i)
            args = ann[ann.find("(") + 1:] if "(" in ann else ""
            path = _first_str(args)
            class_level = m.group(1) == "Request" and any(
                re.search(r"\b(class|interface)\s+\w+", lines[j]) for j in range(i + 1, min(len(lines), i + 7))
                if not lines[j].strip().startswith("@") and lines[j].strip())
            if class_level:
                prefix = path
                continue
            method = m.group(1).upper()
            if method == "REQUEST":
                mm = re.search(r"RequestMethod\.(\w+)", args)
                method = mm.group(1) if mm else "ANY"
            full = (prefix.rstrip("/") + "/" + path.lstrip("/")).rstrip("/") or "/"
            if not path and prefix:
                full = prefix
            out.append({"kind": "http", "name": f"{method} {full}", "file": rel, "line": i + 1,
                        "detail": {"method": method, "path": full, "handler": _handler_after(lines, i),
                                   "framework": "spring"}})
            continue
        m = re.match(r"@Path\s*\(\s*\"([^\"]*)\"", s)
        if m:
            nxt = " ".join(lines[i + 1:i + 4])
            mm = re.search(r"@(GET|POST|PUT|DELETE|PATCH)\b", nxt)
            if mm or re.search(r"\b(class|interface)\s+\w+", nxt):
                if mm:
                    out.append({"kind": "http", "name": f"{mm.group(1)} {m.group(1)}", "file": rel, "line": i + 1,
                                "detail": {"method": mm.group(1), "path": m.group(1), "handler": _handler_after(lines, i),
                                           "framework": "jax-rs"}})
                else:
                    prefix = m.group(1)
            continue
        if re.match(r"@Scheduled\b", s):
            ann = _join_paren(lines, i)
            out.append({"kind": "schedule", "name": ann[ann.find("(") + 1:ann.rfind(")")].strip() or "Scheduled",
                        "file": rel, "line": i + 1, "detail": {"handler": _handler_after(lines, i)}})
            continue
        m = re.match(r"@(KafkaListener|RabbitListener|JmsListener|RocketMQMessageListener)\b", s)
        if m:
            ann = _join_paren(lines, i)
            mt = re.search(r'(?:topics|queues|destination|topic)\s*=\s*(\{[^}]*\}|"[^"]*")', ann)
            topics = re.findall(r'"([^"]*)"', mt.group(1)) if mt else re.findall(r'"([^"]*)"', ann)[:1]
            out.append({"kind": "mq", "name": ",".join(topics) or m.group(1), "file": rel, "line": i + 1,
                        "detail": {"listener": m.group(1), "topics": topics, "handler": _handler_after(lines, i)}})
            continue
        if re.match(r"@(EventListener|TransactionalEventListener)\b", s):
            out.append({"kind": "event", "name": _handler_after(lines, i) or "EventListener", "file": rel,
                        "line": i + 1, "detail": {"handler": _handler_after(lines, i)}})
            continue
        if re.match(r"@DubboService\b", s):
            out.append({"kind": "rpc", "name": "DubboService", "file": rel, "line": i + 1, "detail": {"framework": "dubbo"}})
            continue
        if re.search(r"public\s+static\s+void\s+main\s*\(", s):
            out.append({"kind": "main", "name": "main", "file": rel, "line": i + 1, "detail": {}})
    return out


def java_models(rel, text):
    out = []
    if rel.endswith(".xml"):
        m = re.search(r'<mapper\s+namespace="([^"]+)"', text)
        if m:
            stmts = [(k, sid) for k, sid in re.findall(r"<(select|insert|update|delete)\s+id=\"(\w+)\"", text)]
            out.append({"kind": "mapper", "name": m.group(1).rsplit(".", 1)[-1], "file": rel,
                        "line": text.count("\n", 0, m.start()) + 1,
                        "detail": {"namespace": m.group(1), "statements": [f"{k}:{s}" for k, s in stmts]}})
        return out
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if re.match(r"@(Entity|Document|TableName)\b", s):
            table, cls = None, None
            for j in range(i, min(len(lines), i + 8)):
                t = lines[j].strip()
                mt = re.match(r"@Table\s*\(", t) or re.match(r"@Document\s*\(", t) or re.match(r"@TableName\s*\(", t)
                if mt:
                    ann = _join_paren(lines, j)
                    mm = re.search(r'(?:name|collection|value)\s*=\s*"([^"]*)"', ann) or re.search(r'"([^"]*)"', ann)
                    table = mm.group(1) if mm else table
                mc = re.search(r"\b(class|record)\s+(\w+)", t)
                if mc:
                    cls = mc.group(2)
                    break
            if cls:
                out.append({"kind": "entity", "name": cls, "file": rel, "line": i + 1,
                            "detail": {"table": table, "orm": s[1:].split("(")[0]}})
    return out


JAVA_EXT_RX = re.compile(r"\b(RestTemplate|WebClient|OkHttpClient|HttpClient|FeignClient|DubboReference)\b")
GO_EXT_RX = re.compile(r"(http\.NewRequest(?:WithContext)?\(|http\.(?:Get|Post)\(|grpc\.(?:Dial|NewClient)\(|resty\.New\()")
GO_MQ_IMPORT_RX = re.compile(r'"[^"]*(?:kafka|sarama|amqp|rabbitmq|nats|pulsar|rocketmq|mqtt|pubsub)[^"]*"')
GO_CRON_IMPORT_RX = re.compile(r'"[^"]*(?:/cron|gocron|robfig)[^"]*"')
PY_EXT_RX = re.compile(r"\b((?:requests|httpx|aiohttp)\.(?:get|post|put|delete|patch|request|Client|AsyncClient|ClientSession)\(|grpc\.(?:insecure|secure)_channel\(|boto3\.client\()")


def externals(rel, text, lang):
    rx = {"java": JAVA_EXT_RX, "go": GO_EXT_RX, "python": PY_EXT_RX}.get(lang)
    if not rx:
        return []
    seen = {}
    for i, line in enumerate(text.splitlines(), 1):
        for m in rx.finditer(line):
            name = m.group(1).rstrip("(")
            if name not in seen:
                extra = {}
                if name == "FeignClient":
                    extra["target"] = _first_str(_join_paren(text.splitlines(), i - 1))
                seen[name] = {"kind": "external", "name": name, "file": rel, "line": i, "detail": dict(count=0, **extra)}
            seen[name]["detail"]["count"] += 1
    return list(seen.values())


GO_ROUTE_RX = re.compile(r"\b(\w+)\.(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|Any|Handle|HandleFunc|Get|Post|Put|Delete|Patch|Group)\s*\(\s*\"(/[^\"]*)\"")  # 路径必须以 / 开头，避免 Header.Get(\"X\") 误报


def go_framework(text):
    for key, name in (("gin-gonic/gin", "gin"), ("labstack/echo", "echo"), ("go-chi/chi", "chi"),
                      ("gofiber/fiber", "fiber"), ("gorilla/mux", "gorilla"), ('"net/http"', "net/http")):
        if key in text:
            return name
    return None


def go_entries(rel, text):
    out, lines = [], text.splitlines()
    fw = go_framework(text)
    has_mq, has_cron = bool(GO_MQ_IMPORT_RX.search(text)), bool(GO_CRON_IMPORT_RX.search(text))
    prefix = {}  # 路由组变量 → 路径前缀
    for i, line in enumerate(lines):
        s = line.strip()
        mg = re.match(r"(\w+)\s*:?=\s*(\w+)\.Group\s*\(\s*\"([^\"]*)\"", s)
        if mg:
            prefix[mg.group(1)] = prefix.get(mg.group(2), "") + mg.group(3)
            continue
        if re.match(r"^func\s+main\s*\(\s*\)", s):
            out.append({"kind": "main", "name": "main", "file": rel, "line": i + 1, "detail": {}})
            continue
        m = re.match(r"http\.Handle(Func)?\s*\(\s*\"(/[^\"]*)\"", s)
        if m:
            out.append({"kind": "http", "name": f"ANY {m.group(2)}", "file": rel, "line": i + 1,
                        "detail": {"method": "ANY", "path": m.group(2), "framework": "net/http"}})
            continue
        m = GO_ROUTE_RX.search(s)
        if m and m.group(1) != "http":
            method = m.group(2).upper()
            if method in ("HANDLE", "HANDLEFUNC", "ANY"):
                method = "ANY"
            if method == "GROUP":
                continue
            hm = re.search(r",\s*([\w.]+)\s*\)?\s*$", s)
            full = prefix.get(m.group(1), "") + m.group(3)
            out.append({"kind": "http", "name": f"{method} {full}", "file": rel, "line": i + 1,
                        "detail": {"method": method, "path": full, "handler": hm.group(1) if hm else None,
                                   "framework": fw}})
            continue
        m = re.search(r"\bRegister(\w+)Server\s*\(", s)
        if m:
            out.append({"kind": "rpc", "name": m.group(1), "file": rel, "line": i + 1, "detail": {"framework": "grpc"}})
            continue
        if "&cobra.Command{" in s:
            use = None
            for j in range(i, min(len(lines), i + 4)):
                mu = re.search(r"Use:\s*\"([^\"]*)\"", lines[j])
                if mu:
                    use = mu.group(1)
                    break
            out.append({"kind": "cli", "name": use or "cobra.Command", "file": rel, "line": i + 1, "detail": {"framework": "cobra"}})
            continue
        if has_cron and re.search(r"cron\.New\(|\.AddFunc\(\s*\"|gocron\.", s):
            mm = re.search(r"AddFunc\(\s*\"([^\"]*)\"", s)
            out.append({"kind": "schedule", "name": mm.group(1) if mm else "cron", "file": rel, "line": i + 1, "detail": {}})
            continue
        if has_mq and re.search(r"\.Subscribe\(|kafka\.NewReader\(|amqp\.Dial\(|\.Consume\(", s):
            out.append({"kind": "mq", "name": _first_str(s) or "subscribe", "file": rel, "line": i + 1, "detail": {}})
    return out


def go_models(rel, text):
    out, lines = [], text.splitlines()
    i = 0
    table_names = dict(re.findall(r"func\s*\(\s*(?:\w+\s+)?\*?(\w+)\s*\)\s+TableName\(\)\s+string\s*\{\s*return\s+\"(\w+)\"", text))
    while i < len(lines):
        m = re.match(r"^type\s+(\w+)\s+struct\s*\{", lines[i])
        if m:
            j, tags = i + 1, set()
            while j < len(lines) and not lines[j].startswith("}"):
                for t in re.findall(r"`[^`]*\b(gorm|db|bson)\s*:\"", lines[j]):
                    tags.add(t)
                j += 1
            if tags:
                out.append({"kind": "entity", "name": m.group(1), "file": rel, "line": i + 1,
                            "detail": {"table": table_names.get(m.group(1)), "orm": "/".join(sorted(tags))}})
            i = j
        i += 1
    return out


PY_ROUTE_RX = re.compile(r"@(\w+)\.(get|post|put|delete|patch|route|api_route|websocket)\s*\(\s*[\"'](/[^\"']*)")
PY_MODEL_BASE_RX = re.compile(r"\b(Base|DeclarativeBase|db\.Model|models\.Model|BaseModel|SQLModel|Document)\b")


def py_entries(rel, text):
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        m = PY_ROUTE_RX.match(s)
        if m:
            method = m.group(2).upper()
            if method in ("ROUTE", "API_ROUTE"):
                mm = re.search(r"methods\s*=\s*\[([^\]]*)\]", s)
                method = ",".join(re.findall(r"[\"'](\w+)[\"']", mm.group(1))) if mm else "GET"
            handler = None
            for j in range(i + 1, min(len(lines), i + 4)):
                mh = re.match(r"\s*(?:async\s+)?def\s+(\w+)", lines[j])
                if mh:
                    handler = mh.group(1)
                    break
            out.append({"kind": "http", "name": f"{method} {m.group(3)}", "file": rel, "line": i + 1,
                        "detail": {"method": method, "path": m.group(3), "handler": handler, "router": m.group(1)}})
            continue
        if Path(rel).name == "urls.py":
            m = re.match(r"(?:re_)?path\s*\(\s*r?[\"']([^\"']*)[\"']\s*,\s*([\w.]+)", s)
            if m:
                out.append({"kind": "http", "name": f"ANY /{m.group(1)}", "file": rel, "line": i + 1,
                            "detail": {"method": "ANY", "path": "/" + m.group(1), "handler": m.group(2), "framework": "django"}})
                continue
        if re.match(r"@click\.(command|group)\b|@\w+\.command\s*\(", s):
            out.append({"kind": "cli", "name": _handler_py(lines, i) or "command", "file": rel, "line": i + 1, "detail": {"framework": "click"}})
            continue
        if "argparse.ArgumentParser(" in s:
            out.append({"kind": "cli", "name": "argparse", "file": rel, "line": i + 1, "detail": {"framework": "argparse"}})
            continue
        if re.match(r"if\s+__name__\s*==\s*[\"']__main__[\"']", s):
            out.append({"kind": "main", "name": "__main__", "file": rel, "line": i + 1, "detail": {}})
            continue
        if re.match(r"@(?:shared_task|\w+\.task)\b", s):
            out.append({"kind": "mq", "name": _handler_py(lines, i) or "task", "file": rel, "line": i + 1, "detail": {"framework": "celery"}})
            continue
        if re.search(r"\.scheduled_job\(|(?:Background|AsyncIO|Blocking)Scheduler\(", s):
            out.append({"kind": "schedule", "name": _handler_py(lines, i) or "scheduler", "file": rel, "line": i + 1, "detail": {"framework": "apscheduler"}})
    return out


def _handler_py(lines, i):
    for j in range(i + 1, min(len(lines), i + 4)):
        m = re.match(r"\s*(?:async\s+)?def\s+(\w+)", lines[j])
        if m:
            return m.group(1)
    return None


def py_models(rel, text):
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"class\s+(\w+)\s*\(([^)]*)\)", line.strip())
        if m and PY_MODEL_BASE_RX.search(m.group(2)):
            table = None
            for j in range(i + 1, min(len(lines), i + 12)):
                mt = re.match(r"\s*__tablename__\s*=\s*[\"'](\w+)", lines[j])
                if mt:
                    table = mt.group(1)
                    break
                if lines[j].strip().startswith("class "):
                    break
            base = PY_MODEL_BASE_RX.search(m.group(2)).group(1)
            out.append({"kind": "entity", "name": m.group(1), "file": rel, "line": i + 1,
                        "detail": {"table": table, "orm": base}})
        elif i > 0 and lines[i - 1].strip().startswith("@dataclass"):
            mc = re.match(r"class\s+(\w+)", line.strip())
            if mc:
                out.append({"kind": "dataclass", "name": mc.group(1), "file": rel, "line": i + 1, "detail": {"table": None, "orm": "dataclass"}})
    return out


def imports_of(rel, text, lang):
    if lang == "java":
        return re.findall(r"^\s*import\s+(?:static\s+)?([\w.]+)", text, re.M)
    if lang == "go":
        out = re.findall(r'^import\s+"([^"]+)"', text, re.M)
        for block in re.findall(r"^import\s*\((.*?)^\)", text, re.M | re.S):
            out += re.findall(r'"([^"]+)"', block)
        return out
    if lang == "python":
        out = []
        for m in re.finditer(r"^\s*(?:from\s+(\.*[\w.]*)\s+import|import\s+([\w.]+(?:\s*,\s*[\w.]+)*))", text, re.M):
            if m.group(1) is not None:
                out.append(m.group(1))
            else:
                out += [x.strip() for x in m.group(2).split(",")]
        return out
    return []


def extract_all(ctx, files, texts, fmod, modules, build):
    entries, models, exts, imports = [], [], [], {}
    for rel in files:
        lg = lang_of(rel)
        text = texts.get(rel)
        if rel.endswith(".xml") and "<mapper" in (text or ""):
            for f in java_models(rel, text):
                f["module"] = fmod.get(rel) or _dir_module(rel, modules)
                models.append(f)
            continue
        if not lg or text is None or is_test_file(rel):
            continue
        mid = fmod.get(rel)
        fn_e = {"java": java_entries, "go": go_entries, "python": py_entries}[lg]
        fn_m = {"java": java_models, "go": go_models, "python": py_models}[lg]
        for f in fn_e(rel, text):
            f["module"] = mid
            entries.append(f)
        for f in fn_m(rel, text):
            f["module"] = mid
            models.append(f)
        for f in externals(rel, text, lg):
            f["module"] = mid
            exts.append(f)
        imports[rel] = imports_of(rel, text, lg)
    return entries, models, exts, imports


def _dir_module(rel, modules):
    d = str(Path(rel).parent)
    best = None
    for m in modules.values():
        for md in m["dirs"]:
            if d == md or d.startswith(md + "/"):
                if best is None or len(md) > len(best[1]):
                    best = (m["id"], md)
    return best[0] if best else None


def build_edges(files, fmod, modules, imports, build, texts):
    pkg_map = {}
    for m in modules.values():
        for p in m["packages"]:
            pkg_map[p] = m["id"]
    dir_map = {}
    for rel, mid in fmod.items():
        if mid:
            dir_map[str(Path(rel).parent)] = mid
    gomod = next((m["name"] for m in build["manifests"] if m["ecosystem"] == "go" and m["name"]), None)
    py_roots = {}
    for m in modules.values():
        if m["lang"] == "python":
            for p in m["packages"]:
                py_roots[p] = m["id"]
    edges = Counter()
    for rel, imps in imports.items():
        src = fmod.get(rel)
        if not src:
            continue
        lg = lang_of(rel)
        for imp in imps:
            tgt = None
            if lg == "java":
                parts = imp.split(".")
                for k in range(len(parts) - 1, 0, -1):
                    if ".".join(parts[:k]) in pkg_map:
                        tgt = pkg_map[".".join(parts[:k])]
                        break
            elif lg == "go" and gomod and imp.startswith(gomod):
                d = imp[len(gomod):].strip("/") or "."
                tgt = dir_map.get(d)
            elif lg == "python":
                if imp.startswith("."):
                    lvl = len(imp) - len(imp.lstrip("."))
                    my = _py_dotted(rel, py_roots)
                    if my:
                        base = my.split(".")[:-lvl] if lvl <= len(my.split(".")) else []
                        imp = ".".join(base + ([imp.lstrip(".")] if imp.lstrip(".") else []))
                parts = imp.split(".")
                for k in range(len(parts), 0, -1):
                    if ".".join(parts[:k]) in py_roots:
                        tgt = py_roots[".".join(parts[:k])]
                        break
            if tgt and tgt != src:
                edges[(src, tgt)] += 1
    out = [{"from": a, "to": b, "count": n} for (a, b), n in sorted(edges.items())]
    return out, find_cycles(out)


def _py_dotted(rel, py_roots):
    """src/shop/api/routes.py → shop.api.routes（按已知包根）。"""
    parts = rel[:-3].split("/") if rel.endswith(".py") else rel.split("/")
    for i in range(len(parts)):
        cand = ".".join(parts[i:])
        for root in py_roots:
            if cand == root or cand.startswith(root + "."):
                return cand
    return None


def find_cycles(edges):
    g = defaultdict(set)
    for e in edges:
        g[e["from"]].add(e["to"])
    cycles, seen = [], set()
    state = {}

    def dfs(n, stack):
        state[n] = 1
        stack.append(n)
        for m in sorted(g[n]):
            if state.get(m) == 1:
                cyc = stack[stack.index(m):]
                key = tuple(sorted(cyc))
                if key not in seen:
                    seen.add(key)
                    cycles.append(cyc)
            elif not state.get(m):
                dfs(m, stack)
        stack.pop()
        state[n] = 2
    for n in sorted(g):
        if not state.get(n):
            dfs(n, [])
    return cycles


def detect_configs(files, fmod, modules):
    out = []
    for rel in files:
        if any(F.glob_match(rel, p) for p in CONFIG_PATTERNS):
            out.append({"file": rel, "module": fmod.get(rel) or _dir_module(rel, modules)})
    return out


# ---------------------------------------------------------------- 命令：scan

def cmd_scan(ctx, a):
    ctx.need_init()
    plan = load_json(kb_dir(ctx) / "plan.json", {})
    files, skipped = enumerate_files(ctx, plan.get("scope"))
    texts = {rel: read_text(ctx.root / rel) for rel in files
             if lang_of(rel) or Path(rel).name in ("pom.xml", "go.mod", "pyproject.toml") or rel.endswith(".xml")}
    build = detect_build(ctx, files)
    modules, fmod, tier, notes = cluster_modules(ctx, files, build, texts)
    entries, models, exts, imports = extract_all(ctx, files, texts, fmod, modules, build)
    edges, cycles = build_edges(files, fmod, modules, imports, build, texts)
    configs = detect_configs(files, fmod, modules)
    for f in entries:
        if f["module"] in modules and f["kind"] in REAL_ENTRY_KINDS:
            modules[f["module"]]["entries"] += 1
    for f in models:
        if f["module"] in modules:
            modules[f["module"]]["models"] += 1
    langs = defaultdict(lambda: {"files": 0, "loc": 0})
    for rel in files:
        lg = lang_of(rel)
        if lg and not is_test_file(rel):
            langs[lg]["files"] += 1
            langs[lg]["loc"] += texts[rel].count("\n") + 1
    tests = {"total": sum(1 for r in files if is_test_file(r) and lang_of(r)),
             "by_module": {m["id"]: m["tests"] for m in modules.values() if m["tests"]},
             "frameworks": _test_frameworks(files, texts)}
    # 模块的文件清单（供 plan 用）
    mod_files = defaultdict(list)
    for rel, mid in fmod.items():
        if mid:
            mod_files[mid].append(rel)
    for m in modules.values():
        m["file_list"] = sorted(mod_files.get(m["id"], []))
    scan = {"version": 1, "generated_at": F.now(), "commit": head_sha(), "tier": tier, "languages": dict(langs),
            "build": build, "modules": sorted(modules.values(), key=lambda m: (-m["files"], m["id"])),
            "entries": entries, "models": models, "configs": configs, "externals": exts, "tests": tests,
            "edges": edges,
            "stats": {"source_files": sum(v["files"] for v in langs.values()), "all_files": len(files),
                      "skipped": dict(skipped), "cycles": cycles, "notes": notes}}
    dump(kb_dir(ctx) / "scan.json", scan)
    if a.json:
        print(json.dumps(scan, ensure_ascii=False, indent=2))
        return
    print(f"已扫描 {len(files)} 个文件（源码 {scan['stats']['source_files']}），分级 {tier}，写入 .ai/kb/scan.json")
    lang_txt = ", ".join(f"{k} {v['files']} 文件" for k, v in langs.items()) or "未识别 Java/Go/Python"
    print(f"语言：{lang_txt}")
    print(f"构建：{', '.join(build['ecosystems']) or '未识别'}；入口 {len(entries)}，模型 {len(models)}，配置 {len(configs)}，外部调用 {len(exts)}，边 {len(edges)}")
    if scan["modules"]:
        print(f"{'模块':<24}{'语言':<8}{'文件':>5}{'入口':>5}{'模型':>5}{'测试':>5}  目录")
        for m in scan["modules"]:
            print(f"{m['id']:<24}{m['lang']:<8}{m['files']:>5}{m['entries']:>5}{m['models']:>5}{m['tests']:>5}  {', '.join(m['dirs'][:3])}{'…' if len(m['dirs']) > 3 else ''}")
    for c in cycles:
        print(f"警告：循环依赖 {' -> '.join(c)} -> {c[0]}")
    for n in notes:
        print(f"注意：{n}")
    cmds = build["commands"]
    if any(cmds.values()):
        print("命令（均为猜测，需实际跑过再写进文档）：")
        for k, v in cmds.items():
            if v:
                print(f"  {k:<9}{v['cmd']}    ← {v['why']}")
    print("下一步：flowctl kb plan")


def _test_frameworks(files, texts):
    fw = set()
    for rel in files:
        if not is_test_file(rel):
            continue
        t = texts.get(rel, "")
        if "org.junit" in t:
            fw.add("junit")
        if "testify" in t:
            fw.add("testify")
        if rel.endswith("_test.go"):
            fw.add("go test")
        if "import pytest" in t or "pytest" in t and rel.endswith(".py"):
            fw.add("pytest")
        if "unittest" in t and rel.endswith(".py"):
            fw.add("unittest")
    return sorted(fw)


# ---------------------------------------------------------------- 命令：plan

def need_scan(ctx):
    s = load_json(kb_dir(ctx) / "scan.json")
    if not s:
        F.die("还没有 scan.json，先运行 flowctl kb scan")
    return s


# ---------------------------------------------------------------- 页面模型：三层（architecture / modules / domains）+ 速查

# kind → (相对路径模板, 标题, 一句话目标, 是否必须有 mermaid 图)
ARCH_PAGES = [
    ("arch-overview", "architecture/overview.md", "系统架构总览", "系统边界、核心模块、关键链路、系统架构图", True),
    ("business-flows", "architecture/business-flows.md", "业务主链路与旁路", "每条链路一张流程图，标出经过的模块与关键代码位置", True),
    ("module-dependencies", "architecture/module-dependencies.md", "模块依赖关系", "模块依赖图、依赖边表、循环依赖与排查顺序", True),
    ("interfaces", "architecture/interfaces.md", "对外入口总览", "HTTP / RPC / MQ / 定时 / CLI 按模块分组，代表性入口与数量", False),
    ("data-model", "architecture/data-model.md", "数据模型", "核心实体与表、实体关系图、新增场景要配哪些表", True),
    ("tech-stack", "architecture/tech-stack.md", "技术栈与基础设施", "语言、框架、中间件客户端及版本，以 manifest 为准", False),
    ("config-and-dependencies", "architecture/config-and-dependencies.md", "配置与外部依赖", "配置文件路径、外部系统调用、存储与中间件", False),
    ("patterns", "architecture/patterns.md", "横切机制与协作模式", "错误处理、日志、鉴权、事务、配置加载各怎么做", False),
    ("domain-concepts", "architecture/domain-concepts.md", "跨模块术语与易混概念", "项目黑话、缩写、和字面不一致的命名", False),
    ("dev-guide", "architecture/dev-guide.md", "开发与排查指引", "构建 / 测试 / lint 命令，加接口、加字段的落点，排查顺序", False),
]
DOMAIN_SUBPAGES = [("domain", "README.md", "业务域 SDD", True), ("domain-flow", "核心流程.md", "核心流程摘要", True),
                   ("domain-terms", "术语梳理.md", "术语梳理", False), ("domain-config", "配置清单.md", "配置清单", False)]
DIAGRAM_REQUIRED = {k for k, _, _, _, d in ARCH_PAGES if d} | {k for k, _, _, d in DOMAIN_SUBPAGES if d}
PAGE_KINDS = ("quickref",) + tuple(k for k, *_ in ARCH_PAGES) + ("module",) + tuple(k for k, *_ in DOMAIN_SUBPAGES)
LEGACY_KINDS = ("overview", "architecture", "modules", "glossary")  # 更早版本的页，lint 只提示
README_NAMES = ("README.md", "index.md")  # 自动生成的导航页，不参与 lint / 召回
LINE_LIMITS = {"module": 110, "domain": 220, "domain-flow": 130, "quickref": 130}
DEFAULT_LIMITS = {"max_module_pages": 20, "max_domains": 4, "min_domain_entries": 2, "max_sources": 30}
FACT_CAP = 25


def page_id_for(rel):
    """由路径推出 id：architecture/overview.md → arch-overview；modules/x.md → module-x；domains/d/README.md → domain-d。"""
    parts = rel[:-3].split("/") if rel.endswith(".md") else rel.split("/")
    if parts[0] == "architecture" and len(parts) == 2:
        return "arch-overview" if parts[1] == "overview" else parts[1]
    if parts[0] == "modules" and len(parts) == 2:
        return "module-" + parts[1]
    if parts[0] == "domains" and len(parts) == 3:
        sub = {"README": "", "核心流程": "-flow", "术语梳理": "-terms", "配置清单": "-config"}.get(parts[2], "-" + parts[2])
        return f"domain-{parts[1]}{sub}"
    return "-".join(parts)


def slugify(s):
    s = re.sub(r"[^\w.\-]+", "-", s.strip()).strip("-.").lower()
    return s or "x"


def real_entries(scan, mid=None):
    return [e for e in scan["entries"] if e["kind"] in REAL_ENTRY_KINDS and (mid is None or e["module"] == mid)]


def _module_page(m, scan, limits):
    mid = m["id"]
    ents = real_entries(scan, mid)
    mods = [x for x in scan["models"] if x["module"] == mid]
    first = [e["file"] for e in ents] + [x["file"] for x in mods]
    rest = [f for f in m["file_list"] if f not in first and not is_test_file(f)]
    sources = list(dict.fromkeys(first + rest))
    cap = limits.get("max_sources", DEFAULT_LIMITS["max_sources"])
    kinds = Counter(e["kind"] for e in ents)
    hints = []
    if ents:
        hints.append("入口 " + "、".join(f"{n} 个 {k}" for k, n in kinds.most_common()))
    if mods:
        hints.append("模型 " + "、".join(x["name"] for x in mods[:6]) + ("…" if len(mods) > 6 else ""))
    deps = sorted({e["to"] for e in scan["edges"] if e["from"] == mid})
    if deps:
        hints.append("依赖 " + "、".join(deps))
    users = sorted({e["from"] for e in scan["edges"] if e["to"] == mid})
    if users:
        hints.append("被依赖 " + "、".join(users))
    safe = slugify(mid)
    return {"id": f"module-{safe}", "kind": "module", "module": mid, "path": f"modules/{safe}.md", "name": m["name"],
            "goal": f"{m['name']}：模块信息、职责、关键入口、核心类型、上下游、配置与风险", "hints": hints,
            "entries": len(ents), "models": len(mods), "files": m["files"],
            "sources": sources[:cap], "sources_truncated": len(sources) > cap,
            "paths": [d + "/**" if d != "." else "*" for d in m["dirs"]], "status": "planned"}


def _entry_group(e):
    """把入口归到业务组：HTTP 按去掉 /api/vN 后的首段；CLI 按命令首词；MQ 按 topic；定时归 jobs。"""
    d = e["detail"]
    if e["kind"] == "http":
        path = re.sub(r"^(/api)?(/v\d+)?", "", d.get("path") or "")
        segs = [s for s in path.split("/") if s and not s.startswith((":", "{", "*")) and not re.fullmatch(r"v\d+|api|internal|public", s)]
        return ("http", segs[0]) if segs else None
    if e["kind"] == "cli":
        return ("cli", (e["name"] or "cli").split()[0])
    if e["kind"] == "mq":
        return ("mq", (d.get("topics") or [e["name"]])[0])
    if e["kind"] == "schedule":
        return ("schedule", "jobs")
    if e["kind"] == "rpc":
        return ("rpc", e["name"])
    return None


def domain_candidates(scan, limits):
    groups = defaultdict(list)
    for e in real_entries(scan):
        g = _entry_group(e)
        if g:
            groups[g].append(e)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    out = []
    for (kind, key), ents in ranked:
        if len(ents) < limits["min_domain_entries"] or len(out) >= limits["max_domains"]:
            break
        out.append(_domain_spec(slugify(key), key, ents, scan, limits, kind))
    return out


def _domain_spec(slug, name, ents, scan, limits, kind="http"):
    mods = sorted({e["module"] for e in ents if e["module"]})
    files = list(dict.fromkeys(e["file"] for e in ents))
    cap = limits.get("max_sources", DEFAULT_LIMITS["max_sources"])
    dirs = sorted({d for m in scan["modules"] if m["id"] in mods for d in m["dirs"]})
    return {"slug": slug, "name": name, "group": kind, "entries": len(ents), "modules": mods,
            "sample": [e["name"] for e in ents[:5]], "sources": files[:cap], "paths": [d + "/**" if d != "." else "*" for d in dirs]}


def _domain_pages(spec):
    pages = []
    for kind, fname, title, _ in DOMAIN_SUBPAGES:
        suffix = {"domain": "", "domain-flow": "-flow", "domain-terms": "-terms", "domain-config": "-config"}[kind]
        pages.append({"id": f"domain-{spec['slug']}{suffix}", "kind": kind, "domain": spec["slug"], "name": spec["name"],
                      "path": f"domains/{spec['slug']}/{fname}", "goal": f"{spec['name']}：{title}",
                      "hints": [f"{spec['entries']} 个入口（{spec['group']}），涉及模块 {', '.join(spec['modules']) or '待确认'}",
                                "代表入口 " + "、".join(spec["sample"])],
                      "modules": spec["modules"], "sources": spec["sources"] if kind in ("domain", "domain-flow") else [],
                      "paths": spec["paths"], "status": "planned"})
    return pages


def _fixed_pages(scan, limits):
    man = [m["file"] for m in scan["build"]["manifests"]]
    cfg = [c["file"] for c in scan["configs"]][:20]
    all_dirs = sorted({d for m in scan["modules"] for d in m["dirs"]})
    all_paths = [d + "/**" if d != "." else "*" for d in all_dirs]
    ent_files = list(dict.fromkeys(e["file"] for e in real_entries(scan)))
    mod_files = list(dict.fromkeys(x["file"] for x in scan["models"]))
    ext_files = list(dict.fromkeys(x["file"] for x in scan["externals"]))
    cap = limits.get("max_sources", DEFAULT_LIMITS["max_sources"])
    src = {"arch-overview": man, "business-flows": ent_files, "module-dependencies": man, "interfaces": ent_files,
           "data-model": mod_files, "tech-stack": man, "config-and-dependencies": cfg + ext_files, "patterns": [],
           "domain-concepts": mod_files, "dev-guide": man}
    pages = [{"id": "ai-quick-reference", "kind": "quickref", "path": "ai-quick-reference.md", "name": "AI 快速参考",
              "goal": "三句话理解系统、按问题找文档、模块速查、检索顺序、边界提醒", "sources": man, "paths": ["*"], "status": "planned"}]
    for kind, path, title, goal, _ in ARCH_PAGES:
        pid = "arch-overview" if kind == "arch-overview" else kind
        pages.append({"id": pid, "kind": kind, "path": path, "name": title, "goal": goal, "sources": src[kind][:cap],
                      "paths": all_paths if kind in ("arch-overview", "business-flows", "module-dependencies", "dev-guide") else
                      ["*"] if kind in ("tech-stack",) else all_paths, "status": "planned"})
    return pages


def cmd_plan(ctx, a):
    ctx.need_init()
    scan = need_scan(ctx)
    pp = kb_dir(ctx) / "plan.json"
    old = load_json(pp, {})
    limits = dict(DEFAULT_LIMITS, **(old.get("limits") or {}))
    old_pages = {p["id"]: p for p in old.get("pages", [])}
    # 模块页：全部模块（按文件数排序，超出 max_module_pages 的并不生成）
    mods = sorted((m for m in scan["modules"] if m["files"] > 0), key=lambda m: (-m["files"], m["id"]))
    module_pages = [_module_page(m, scan, limits) for m in mods[: limits["max_module_pages"]]]
    # 业务域：scan 推荐 + 用户在 plan.json 里手加的（{"id":"domain-x","kind":"domain","name":"x"}）
    specs = {d["slug"]: d for d in domain_candidates(scan, limits)}
    for pid, o in old_pages.items():
        if o.get("kind") == "domain" and o.get("status") in ("planned", "proposed"):
            slug = o.get("domain") or pid[len("domain-"):]
            if slug not in specs:
                ents = [e for e in real_entries(scan) if e["module"] in (o.get("modules") or [])]
                specs[slug] = _domain_spec(slug, o.get("name") or slug, ents, scan, limits, "manual")
            else:
                specs[slug]["name"] = o.get("name") or specs[slug]["name"]
    domain_pages = [p for slug in specs for p in _domain_pages(specs[slug])]
    fresh = _fixed_pages(scan, limits) + module_pages + domain_pages
    pages = []
    for p in fresh:
        o = old_pages.pop(p["id"], None)
        if o:
            if o.get("status") == "removed":
                pages.append(o)
                continue
            keep = ("goal", "hints", "paths", "status", "name") if not p["kind"].startswith("domain-") else ("hints", "paths", "status")
            for k in keep:  # 域子页的 name / goal 跟随 README 那条
                if k in o:
                    p[k] = o[k]
            if p.get("status") == "orphan":
                p["status"] = "planned"
        elif old.get("pages"):
            p["status"] = "proposed"
        pages.append(p)
    for o in old_pages.values():  # scan 里不再出现的
        if o.get("status") != "removed":
            o["status"] = "orphan"
        pages.append(o)
    # 某个域的 README 被标 removed，其子页一并跟随
    removed_domains = {p.get("domain") for p in pages if p.get("kind") == "domain" and p.get("status") == "removed"}
    for p in pages:
        if p.get("kind", "").startswith("domain-") and p.get("domain") in removed_domains:
            p["status"] = "removed"
    plan = {"version": 3, "tier": scan["tier"], "generated_at": F.now(), "limits": limits,
            "scope": old.get("scope") or {"include": [], "exclude": []}, "notes": old.get("notes") or [], "pages": pages}
    dump(pp, plan)
    if a.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    active = [p for p in pages if p["status"] != "removed"]
    n_mod = sum(1 for p in active if p["kind"] == "module")
    n_dom = sum(1 for p in active if p["kind"] == "domain")
    print(f"页面规划（{scan['tier']}）：写入 .ai/kb/plan.json —— 速查 1 + 架构 {len(ARCH_PAGES)} + 模块 {n_mod} + 业务域 {n_dom}×4 页，另有 4 个自动生成的 README")
    print(f"{'id':<34}{'kind':<20}{'对象':<24}{'入口':>5}  {'status':<9} goal")
    for p in pages:
        obj = p.get("module") or p.get("name", "") if p["kind"] != "quickref" else ""
        print(f"{p['id']:<34}{p['kind']:<20}{str(obj)[:23]:<24}{p.get('entries', ''):>5}  {p['status']:<9} {p.get('goal', '')[:60]}")
    if specs:
        print("业务域候选（按入口分组推荐，请改名、合并或删除）：" + "；".join(
            f"{s['name']}（{s['entries']} 个 {s['group']} 入口，模块 {'/'.join(s['modules'][:3])}）" for s in specs.values()))
    extra = [pg.rel for pg in load_pages(ctx) if pg.rel not in {p["path"] for p in pages}]
    if extra:
        print("不在规划内的已有页：" + "、".join(extra) + "  → 删除，或把有用内容并进对应页后删除")
    print("编辑 plan.json 后重跑 flowctl kb plan：limits 调页数；scope 缩小范围；status 改 removed 删页；domain 页改 name 即改域名；proposed/orphan 需确认。")
    print("下一步：flowctl kb draft --all")


# ---------------------------------------------------------------- 命令：draft / facts

def _yaml_list(items):
    return "[" + ", ".join('"' + str(x).replace('"', "'") + '"' for x in items) + "]"


def load_plan(ctx):
    p = load_json(kb_dir(ctx) / "plan.json")
    if not p:
        F.die("还没有 plan.json，先运行 flowctl kb scan && flowctl kb plan")
    return p


KIND_TRIGGERS = {
    "quickref": ["速查", "入口", "哪里", "怎么找"], "arch-overview": ["架构", "总览", "边界", "链路"],
    "business-flows": ["流程", "链路", "主链", "旁路", "flow"], "module-dependencies": ["依赖", "调用", "循环依赖", "排查"],
    "interfaces": ["接口", "入口", "api", "路由", "endpoint", "消费", "定时", "命令"], "data-model": ["表", "实体", "模型", "字段", "schema", "entity"],
    "tech-stack": ["技术栈", "版本", "框架", "依赖"], "config-and-dependencies": ["配置", "外部", "中间件", "存储", "config"],
    "patterns": ["错误处理", "日志", "鉴权", "事务", "横切"], "domain-concepts": ["术语", "缩写", "概念"],
    "dev-guide": ["构建", "测试", "lint", "怎么加", "排查", "build"],
}


def _triggers_for(page, scan):
    words = []
    kind = page["kind"]
    if kind == "module":
        mid = page["module"]
        words += [mid.split("/")[-1].split(".")[-1]]
        for e in scan["entries"]:
            if e["module"] == mid and e["kind"] == "http":
                words += [w for w in re.split(r"[/{}:*]", e["detail"].get("path", "")) if len(w) >= 3 and not w.isdigit()]
        for x in scan["models"]:
            if x["module"] == mid:
                words.append(x["name"])
                if x["detail"].get("table"):
                    words.append(x["detail"]["table"])
    elif kind.startswith("domain"):
        words = [page.get("name", ""), page.get("domain", "")] + [m.split("/")[-1] for m in page.get("modules", [])]
        words += {"domain-terms": ["术语"], "domain-config": ["配置"], "domain-flow": ["流程"]}.get(kind, [])
    else:
        words = KIND_TRIGGERS.get(kind, [])
    seen, out = set(), []
    for w in words:
        if w and w.lower() not in seen and len(w) >= 2:
            seen.add(w.lower())
            out.append(w)
    return out[:10]


def render_template(kind, page, scan):
    tpl = F.TEMPLATE_DIR / "kb" / f"{kind}.md"
    if not tpl.exists():
        F.die(f"模板不存在：{tpl}")
    text = tpl.read_text(encoding="utf-8")
    fill = {"id": page["id"], "kind": kind, "module": page.get("module", ""), "goal": page.get("goal", ""),
            "name": page.get("name") or page.get("module") or kind, "domain": page.get("domain", ""),
            "hints": "；".join(page.get("hints", [])) or "（无）", "date": F.now()[:10],
            "triggers": _yaml_list(_triggers_for(page, scan)), "paths": _yaml_list(page.get("paths", [])),
            "sources": _yaml_list(page.get("sources", [])), "modules": "、".join(page.get("modules", [])) or "（待确认）"}
    for k, v in fill.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def manual_blocks(text):
    """返回 [(所在 ## 标题或 None, 块全文)]。"""
    out = []
    for m in re.finditer(re.escape(MANUAL_OPEN) + r".*?" + re.escape(MANUAL_CLOSE), text, re.S):
        head = None
        for h in re.finditer(r"^## .+$", text[:m.start()], re.M):
            head = h.group(0)
        out.append((head, m.group(0)))
    return out


def merge_manual(new_text, old_text):
    """把旧页的 kb:manual 块按同名 ## 标题放回新页；找不到标题就追加到末尾。"""
    for head, block in manual_blocks(old_text):
        if block in new_text:
            continue
        if head and head in new_text:
            i = new_text.index(head) + len(head)
            new_text = new_text[:i] + "\n\n" + block + new_text[i:]
        else:
            new_text = new_text.rstrip("\n") + "\n\n## 人工补充\n\n" + block + "\n"
    return new_text


def cmd_draft(ctx, a):
    ctx.need_init()
    scan, plan = need_scan(ctx), load_plan(ctx)
    targets = [p for p in plan["pages"] if p.get("status") in ("planned", "proposed")]
    if a.page:
        targets = [p for p in targets if p["id"] == a.page]
        if not targets:
            F.die(f"plan.json 里没有可草拟的页：{a.page}")
    elif not a.all:
        F.die("需要 --page <id> 或 --all")
    written, skipped = [], []
    for p in targets:
        dst = kb_dir(ctx) / p["path"]
        if dst.exists() and not a.force:
            skipped.append(f"{p['id']}（已存在，需 --force）")
            continue
        text = render_template(p["kind"], p, scan)
        if dst.exists():
            old = dst.read_text(encoding="utf-8")
            meta, _ = F.parse_frontmatter(old)
            if str(meta.get("protected", "")).lower() in ("true", "yes", "on"):
                skipped.append(f"{p['id']}（protected: true）")
                continue
            for k in ("triggers", "paths"):
                if meta.get(k):
                    text = re.sub(rf"^{k}:.*$", f"{k}: {_yaml_list(F._as_list(meta[k]))}", text, count=1, flags=re.M)
            if meta.get("summary") and TODO not in str(meta["summary"]):
                text = re.sub(r"^summary:.*$", f"summary: {meta['summary']}", text, count=1, flags=re.M)
            text = merge_manual(text, old)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        written.append(p["id"])
    for w in written:
        print(f"已写草稿 {w}")
    for s_ in skipped:
        print(f"跳过 {s_}")
    if written:
        print("下一步：对每页执行 flowctl kb facts --page <id> 取事实，按模板写正文；写完 flowctl kb lint")


def _cap(items, n=FACT_CAP):
    return items[:n], max(0, len(items) - n)


def mermaid_deps(scan, module_ids=None):
    """由 import 边生成依赖图骨架。"""
    ids = {m["id"] for m in scan["modules"]}
    if module_ids:
        ids &= set(module_ids)
    alias = {mid: f"m{i}" for i, mid in enumerate(sorted(ids))}
    lines = ["```mermaid", "graph LR"]
    for mid, al in alias.items():
        lines.append(f"  {al}[{mid}]")
    for e in scan["edges"]:
        if e["from"] in alias and e["to"] in alias:
            lines.append(f"  {alias[e['from']]} -->|{e['count']}| {alias[e['to']]}")
    lines.append("```")
    return "\n".join(lines)


def mermaid_er(models):
    lines = ["```mermaid", "erDiagram"]
    seen = set()
    for x in models:
        name = re.sub(r"\W", "_", x["detail"].get("table") or x["name"]).upper()
        if name in seen:
            continue
        seen.add(name)
        lines.append(f"  {name} {{")
        lines.append(f"    string _ \"{x['name']} @ {x['file']}:{x['line']}\"")
        lines.append("  }")
    lines += ["  %% 关系只画有证据的：外键字段、关联注解、join；在这里补 A ||--o{ B : label", "```"]
    return "\n".join(lines)


def _group_entries(entries):
    groups = defaultdict(list)
    for e in entries:
        g = _entry_group(e)
        groups[g if g else (e["kind"], "其他")].append(e)
    return groups


def page_facts(page, scan):
    kind, mid = page["kind"], page.get("module")
    mods_brief = [{k: m[k] for k in ("id", "lang", "files", "dirs", "layout", "entries", "models")} for m in scan["modules"]]
    ents = real_entries(scan)
    if kind == "module":
        return {"entries": [e for e in scan["entries"] if e["module"] == mid],
                "models": [x for x in scan["models"] if x["module"] == mid],
                "externals": [x for x in scan["externals"] if x["module"] == mid],
                "configs": [c for c in scan["configs"] if c["module"] == mid],
                "edges_out": [e for e in scan["edges"] if e["from"] == mid],
                "edges_in": [e for e in scan["edges"] if e["to"] == mid],
                "module": next((m for m in scan["modules"] if m["id"] == mid), None)}
    if kind.startswith("domain"):
        dmods = set(page.get("modules", []))
        slug = page.get("domain", "")
        d_ents = [e for e in ents if _entry_group(e) and slugify(_entry_group(e)[1]) == slug] or [e for e in ents if e["module"] in dmods]
        sel = {"entries": d_ents, "models": [x for x in scan["models"] if x["module"] in dmods],
               "edges": [e for e in scan["edges"] if e["from"] in dmods or e["to"] in dmods],
               "configs": [c for c in scan["configs"] if c["module"] in dmods],
               "externals": [x for x in scan["externals"] if x["module"] in dmods], "domain_modules": sorted(dmods)}
        if kind in ("domain", "domain-flow"):
            sel["mermaid"] = mermaid_deps(scan, dmods) if dmods else None
        return sel
    if kind == "quickref":
        return {"modules": mods_brief, "entry_groups": {f"{k[0]}:{k[1]}": len(v) for k, v in _group_entries(ents).items()},
                "commands": scan["build"]["commands"], "cycles": scan["stats"]["cycles"]}
    if kind == "arch-overview":
        return {"modules": mods_brief, "edges": scan["edges"], "entry_groups": {f"{k[0]}:{k[1]}": len(v) for k, v in _group_entries(ents).items()},
                "externals": scan["externals"][:FACT_CAP], "mermaid": mermaid_deps(scan)}
    if kind == "business-flows":
        groups = _group_entries(ents)
        return {"groups": [{"group": f"{k[0]}:{k[1]}", "count": len(v), "entries": v[:6]} for k, v in
                           sorted(groups.items(), key=lambda kv: -len(kv[1]))[:12]], "edges": scan["edges"]}
    if kind == "module-dependencies":
        return {"edges": scan["edges"], "cycles": scan["stats"]["cycles"], "modules": mods_brief, "mermaid": mermaid_deps(scan)}
    if kind == "interfaces":
        by_mod = defaultdict(list)
        for e in ents:
            by_mod[e["module"]].append(e)
        return {"by_module": [{"module": m, "counts": dict(Counter(e["kind"] for e in v)), "entries": v[:8]}
                              for m, v in sorted(by_mod.items(), key=lambda kv: -len(kv[1]))]}
    if kind == "data-model":
        return {"models": scan["models"], "mermaid": mermaid_er(scan["models"][:40])}
    if kind == "tech-stack":
        return {"build": scan["build"], "languages": scan["languages"]}
    if kind == "config-and-dependencies":
        return {"configs": scan["configs"], "externals": scan["externals"]}
    if kind == "patterns":
        return {"externals": scan["externals"][:FACT_CAP], "hint": "scan 不抽横切机制；用 grep 找统一错误类型、日志封装、鉴权中间件、事务注解、配置加载入口，各给一个 file:line"}
    if kind == "domain-concepts":
        return {"models": scan["models"], "entry_groups": {f"{k[0]}:{k[1]}": len(v) for k, v in _group_entries(ents).items()}}
    if kind == "dev-guide":
        return {"commands": scan["build"]["commands"], "tests": scan["tests"], "modules": mods_brief,
                "entry_sample": ents[:10], "model_sample": scan["models"][:6]}
    return {"entries": ents, "models": scan["models"]}


def _entry_line(e, with_module=True):
    h = e["detail"].get("handler")
    return (f"- [{KIND_LABEL.get(e['kind'], '证据-起点')}] {e['kind']} {e['name']} → {e['file']}:{e['line']}"
            + (f" ({h})" if h else "") + (f" [{e['module']}]" if with_module else ""))


def _model_line(x, with_module=True):
    t = x["detail"].get("table")
    return (f"- [证据-数据] {x['kind']} {x['name']}" + (f" 表 {t}" if t else "") + f" → {x['file']}:{x['line']}"
            + (f" [{x['module']}]" if with_module else ""))


def facts_markdown(page, sel):
    lines = [f"# 事实：{page['id']}（来自 .ai/kb/scan.json，每条带 file:line；没列出的就是 scan 没找到；只挑有代表性的写进页面）"]
    m = sel.get("module")
    wm = m is None
    if m:
        lines.append(f"- 模块 {m['id']}：{m['files']} 个源文件，{m['tests']} 个测试文件，目录 {', '.join(m['dirs'][:6])}，包 {', '.join(m['packages'][:5])}")
    if sel.get("domain_modules") is not None:
        lines.append(f"- 涉及模块：{', '.join(sel['domain_modules']) or '待确认（在 plan.json 的 modules 里补）'}")
    if "entries" in sel:
        shown, more = _cap(sel["entries"])
        lines += [_entry_line(e, wm) for e in shown]
        if more:
            kinds = Counter(e["kind"] for e in sel["entries"])
            lines.append(f"- …另有 {more} 条入口未列（{', '.join(f'{k} {n}' for k, n in kinds.most_common())}），页面按前缀或类型归组，不逐条列")
    if "models" in sel:
        shown, more = _cap(sel["models"])
        lines += [_model_line(x, wm) for x in shown]
        if more:
            lines.append(f"- …另有 {more} 个模型未列，页面只写核心实体")
    for x in sel.get("externals", [])[:FACT_CAP]:
        lines.append(f"- [证据-调用] 外部 {x['name']} ×{x['detail']['count']} → {x['file']}:{x['line']}" + (f" [{x['module']}]" if wm else ""))
    for c in sel.get("configs", [])[:FACT_CAP]:
        lines.append(f"- [证据-配置] {c['file']}（只列路径，不写配置值）")
    for e in sel.get("edges_out", []):
        lines.append(f"- [证据-调用] 依赖 {e['to']}（import ×{e['count']}）")
    for e in sel.get("edges_in", []):
        lines.append(f"- [证据-调用] 被 {e['from']} 依赖（import ×{e['count']}）")
    if "edges" in sel:
        lines.append("- 模块依赖边（from → to ×count）：" + ("; ".join(f"{e['from']} → {e['to']} ×{e['count']}" for e in sel["edges"]) or "无"))
    if "cycles" in sel:
        lines.append("- 循环依赖：" + ("; ".join(" -> ".join(c) for c in sel["cycles"]) if sel["cycles"] else "未发现"))
    if "entry_groups" in sel:
        lines.append("- 入口分组（类型:组 = 数量）：" + "; ".join(f"{k} = {n}" for k, n in sorted(sel["entry_groups"].items(), key=lambda kv: -kv[1])[:20]))
    for g in sel.get("groups", []):
        lines.append(f"## 链路候选 {g['group']}（{g['count']} 个入口）")
        lines += [_entry_line(e) for e in g["entries"]]
    for bm in sel.get("by_module", []):
        lines.append(f"## {bm['module']}：" + ", ".join(f"{k} {n}" for k, n in bm["counts"].items()))
        lines += [_entry_line(e, False) for e in bm["entries"]]
    if "modules" in sel and m is None:
        for mm in sel["modules"]:
            lines.append(f"- 模块 {mm['id']}（{mm['lang']}，{mm['files']} 文件，入口 {mm['entries']}，模型 {mm['models']}）目录 {', '.join(mm['dirs'][:4])}")
    if "build" in sel:
        b = sel["build"]
        for man in b["manifests"]:
            fw = "，".join(f"{k} {v}" for k, v in man.get("frameworks", {}).items())
            lines.append(f"- [证据-配置] manifest {man['file']}:{man['line']} {man['ecosystem']} {man.get('name') or ''} {man.get('version') or ''} {fw}")
            for d in man["dependencies"][:FACT_CAP]:
                lines.append(f"  - 依赖 {d.get('group', '') + ':' if d.get('group') else ''}{d['artifact']} {d.get('version') or '（版本未写）'}{'' if d.get('resolved', True) else '（未解析）'} → {man['file']}:{d['line']}")
            if len(man["dependencies"]) > FACT_CAP:
                lines.append(f"  - …另有 {len(man['dependencies']) - FACT_CAP} 个依赖，页面只列框架与中间件客户端")
        lines.append(f"- 语言统计：{json.dumps(sel.get('languages', {}), ensure_ascii=False)}")
    if "commands" in sel:
        for k, v in sel["commands"].items():
            if v:
                lines.append(f"- 命令 {k}：`{v['cmd']}`（猜测：{v['why']}；必须实际跑过才能写「已验证」）")
    if "tests" in sel:
        lines.append(f"- 测试：{json.dumps(sel['tests'], ensure_ascii=False)}")
    for e in sel.get("entry_sample", []):
        lines.append(_entry_line(e))
    for x in sel.get("model_sample", []):
        lines.append(_model_line(x))
    if sel.get("hint"):
        lines.append(f"- 提示：{sel['hint']}")
    if sel.get("mermaid"):
        lines += ["", "## 图骨架（可直接粘进页面再补标注）", sel["mermaid"]]
    return "\n".join(lines) + "\n"


def cmd_facts(ctx, a):
    ctx.need_init()
    scan, plan = need_scan(ctx), load_plan(ctx)
    if not a.page:
        F.die("需要 --page <id>")
    page = next((p for p in plan["pages"] if p["id"] == a.page), None)
    if not page:
        F.die(f"plan.json 里没有页：{a.page}")
    sel = page_facts(page, scan)
    if a.json:
        print(json.dumps(sel, ensure_ascii=False, indent=2))
    else:
        print(facts_markdown(page, sel), end="")


# ---------------------------------------------------------------- 页面加载 / lint

class Page:
    def __init__(self, path, kbroot):
        self.path = path
        self.rel = str(path.relative_to(kbroot))
        self.text = path.read_text(encoding="utf-8", errors="replace")
        self.meta, self.body = F.parse_frontmatter(self.text)
        self.id = str(self.meta.get("id") or page_id_for(self.rel))
        self.kind = str(self.meta.get("kind") or "")
        self.module = str(self.meta.get("module") or "")
        self.domain = str(self.meta.get("domain") or "")
        self.name = str(self.meta.get("name") or "")
        self.summary = str(self.meta.get("summary") or "")
        self.triggers = [t for t in F._as_list(self.meta.get("triggers")) if t]
        self.paths = [p for p in F._as_list(self.meta.get("paths")) if p]
        self.sources = [s for s in F._as_list(self.meta.get("sources")) if s]
        self.protected = str(self.meta.get("protected", "")).lower() in ("true", "yes", "on")

    def refs(self):
        out = []
        for m in REF_RX.finditer(self.body):
            if self.body[max(0, m.start() - 3):m.start()] == "://":
                continue
            out.append((m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None))
        return out

    def has_diagram(self):
        return "```mermaid" in self.body


def load_pages(ctx):
    root = kb_dir(ctx)
    if not root.is_dir():
        return []
    pages = []
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if p.name.startswith("_") or (p.name in README_NAMES and len(rel.parts) <= 2):
            continue  # 根和三层目录下的 README 是生成的导航；domains/<域>/README.md 是 SDD 正文
        pages.append(Page(p, root))
    return pages


def _line_count_cache():
    cache = {}

    def count(p):
        if p not in cache:
            try:
                t = read_text(p)
                cache[p] = t.count("\n") + (0 if t.endswith("\n") else 1)
            except OSError:
                cache[p] = None
        return cache[p]
    return count


def lint_pages(ctx, pages):
    errors, warns = [], []
    count = _line_count_cache()
    tracked = set(F.git("ls-files").split("\n"))
    ids = Counter(p.id for p in pages)
    for p in pages:
        e = lambda msg: errors.append(f"{p.rel}: {msg}")
        w = lambda msg: warns.append(f"{p.rel}: {msg}")
        want = page_id_for(p.rel)
        if p.id != want:
            e(f"id「{p.id}」与路径不一致（应为 {want}）")
        if ids[p.id] > 1:
            e(f"id 重复：{p.id}")
        if str(p.meta.get("type") or "") != "kb":
            e("缺少 type: kb")
        if p.kind not in PAGE_KINDS and p.kind not in LEGACY_KINDS:
            e(f"kind 不合法：{p.kind or '（空）'}")
        if not p.summary or TODO in p.summary:
            e("summary 未填写（README 靠它生成）")
        if not p.meta.get("updated"):
            e("缺少 updated")
        if p.kind == "module" and not (p.triggers or p.paths):
            e("module 页必须有 triggers 或 paths，否则召回不到")
        if TODO in p.body:
            e(f"正文残留 {TODO}")
        if p.body.count(MANUAL_OPEN) != p.body.count(MANUAL_CLOSE):
            e("kb:manual 标记不成对")
        if not re.search(r"^## 引用文件\s*$", p.body, re.M):
            e("缺少「## 引用文件」一节")
        if p.kind in DIAGRAM_REQUIRED and not p.has_diagram():
            e("核心页必须有 mermaid 图（架构图 / 流程图 / 依赖图 / ER 图）")
        for s_ in p.sources:
            if s_ not in tracked and not (ctx.root / s_).exists():
                e(f"sources 里的文件不存在：{s_}")
        for path, a1, a2 in p.refs():
            full = ctx.root / path
            if path not in tracked and not full.exists():
                e(f"引用了不存在的文件 {path}:{a1}")
                continue
            n = count(full)
            if n is not None and (a1 > n or (a2 and a2 > n)):
                e(f"{path}:{a1}{'-' + str(a2) if a2 else ''} 超出文件行数 {n}")
        for mm in re.finditer(r"```(?!mermaid)[^\n]*\n(.*?)```", p.body, re.S):
            if mm.group(1).count("\n") > 12:
                w(f"代码块超过 12 行（{mm.group(1).count(chr(10))} 行），知识库只放引用不放实现")
        n_lines = p.body.count("\n")
        limit = LINE_LIMITS.get(p.kind, 130)
        if n_lines > limit:
            w(f"正文 {n_lines} 行，超过建议上限 {limit}：只留代表性内容，其余靠 file:line 指路")
        if p.kind in LEGACY_KINDS:
            w("旧版页，已不在规划内：把有用内容并进 architecture/ 对应页后删除")
    plan = load_json(kb_dir(ctx) / "plan.json", {})
    have = {p.rel for p in pages}
    for pp in plan.get("pages", []):
        if pp.get("status") in ("planned", "proposed") and pp["path"] not in have:
            warns.append(f"plan 里的页还没有文件：{pp['path']}")
    return errors, warns


def cmd_lint(ctx, a):
    ctx.need_init()
    pages = load_pages(ctx)
    if not pages:
        F.die("知识库里没有页面（.ai/kb/**/*.md）")
    errors, warns = lint_pages(ctx, pages)
    for x in errors:
        print(f"ERROR {x}")
    for x in warns:
        print(f"WARN  {x}")
    print(f"共 {len(pages)} 页：{len(errors)} 个错误，{len(warns)} 个警告")
    if errors:
        sys.exit(1)


# ---------------------------------------------------------------- freeze：四个 README / status

def _fm(pid, kind):
    return ["---", f"id: {pid}", "type: kb", f"kind: {kind}", f"updated: {F.now()[:10]}", "---", ""]


def _table(rows, header):
    return [header, "|" + "---|" * (header.count("|") - 1)] + rows + [""]


def _cell(s):
    return (s or "").replace("|", "/").replace("\n", " ")


def render_readmes(ctx, pages, scan, old):
    """返回 {相对路径: 文本}。old 是旧 README 文本，用来保留 kb:manual 块。"""
    by_kind = defaultdict(list)
    for p in pages:
        by_kind[p.kind].append(p)
    arch = [p for k, *_ in ARCH_PAGES for p in by_kind.get(k, [])]
    mods = sorted(by_kind.get("module", []), key=lambda p: -(next((m["files"] for m in scan["modules"] if m["id"] == p.module), 0)))
    doms = sorted(by_kind.get("domain", []), key=lambda p: p.id)
    quick = by_kind.get("quickref", [None])[0]
    ov = by_kind.get("arch-overview", [None])[0]
    name = (scan["build"]["manifests"][0].get("name") if scan["build"]["manifests"] else None) or ctx.root.name
    out = {}
    # 根 README
    L = _fm("README", "index") + [f"# {name} 代码知识库", "",
         "<!-- 由 flowctl kb freeze 生成，勿手改正文；人工内容放到末尾的 kb:manual 块里。 -->", ""]
    if ov and ov.summary:
        L += [ov.summary, ""]
    L += [f"> **人类导航**：本文档 · **AI 速查**：[ai-quick-reference.md](ai-quick-reference.md) · 事实来源：代码为准，文档与代码冲突时以当前分支为准并回补本目录（`flowctl kb status` 检测过期）。", ""]
    L += ["## 目录结构", "", "```text", ".ai/kb/", "├── README.md                     ← 本文档（总导航）",
          "├── ai-quick-reference.md         ← AI 首选速查", f"├── architecture/                 ← {len(arch)} 页：总览、链路、依赖、入口、数据、栈、配置、模式、术语、指引",
          f"├── modules/                      ← {len(mods)} 个模块，每个一页", f"└── domains/                      ← {len(doms)} 个业务域，每域 README + 核心流程 + 术语 + 配置",
          "```", ""]
    L += ["## 按角色阅读", "", "| 场景 | 顺序 |", "|---|---|",
          "| 新人入职 | architecture/overview.md → architecture/business-flows.md → modules/README.md → domains/ |",
          "| 需求分析 / 方案 | ai-quick-reference.md → 对应 domains/<域>/README.md → architecture/interfaces.md、data-model.md → 对应 modules/ 页 |",
          "| 问题排查 | architecture/module-dependencies.md → architecture/config-and-dependencies.md → 对应 modules/ 页 |",
          "| 加接口 / 加字段 | architecture/dev-guide.md → domains/<域>/配置清单.md |", ""]
    L += ["## 三层长期知识", "", "| 层 | 目录 | 内容 |", "|---|---|---|",
          "| 架构 | [architecture/](architecture/README.md) | 全局链路、依赖、入口、数据模型、技术栈、开发指引 |",
          "| 模块 | [modules/](modules/README.md) | 每个模块的职责、入口、核心类型、上下游 |",
          "| 业务域 | [domains/](domains/README.md) | 业务流程 SDD、术语、配置 |", ""]
    if mods:
        L += ["## 模块索引", ""] + _table([f"| {p.module} | [{Path(p.rel).name}]({p.rel}) | {_cell(p.summary)} |" for p in mods], "| 模块 | 文档 | 一句话 |")
    if arch:
        L += ["## architecture 文档索引", ""] + _table([f"| [{Path(p.rel).name}]({p.rel}) | {_cell(p.summary)} |" for p in arch], "| 文档 | 内容 |")
    if doms:
        L += ["## 业务域", ""] + _table([f"| {p.name or p.domain} | [README.md]({p.rel}) | [核心流程](domains/{p.domain}/核心流程.md) · [术语](domains/{p.domain}/术语梳理.md) · [配置](domains/{p.domain}/配置清单.md) | {_cell(p.summary)} |" for p in doms], "| 域 | SDD | 配套 | 一句话 |")
    L += ["## 维护说明", "", "- 代码改动后执行 `flowctl kb status`；有过期页就运行 `/flow-kb` 刷新，只重写过期页。",
          "- 新增模块 / 业务域：重跑 `flowctl kb scan` 与 `flowctl kb plan`，确认后 `flowctl kb draft`。",
          "- 人工修订放在 `<!-- kb:manual -->` 块里或把页标 `protected: true`，刷新不会覆盖。", ""]
    out["README.md"] = _with_manual("\n".join(L), old.get("README.md"))
    # architecture/README
    L = _fm("architecture-README", "index") + ["# 架构层导航", "", "> AI 首选：[../ai-quick-reference.md](../ai-quick-reference.md)。本目录回答全局问题：链路、依赖、入口、数据、栈、模式。", ""]
    L += ["## 推荐阅读顺序", "", "```text", "1. overview.md → 2. business-flows.md → 3. module-dependencies.md → 4. interfaces.md / data-model.md → 5. dev-guide.md", "```", ""]
    L += ["## 文档索引", ""] + _table([f"| [{Path(p.rel).name}]({Path(p.rel).name}) | {_cell(p.summary)} | {', '.join(p.triggers[:5])} |" for p in arch], "| 文档 | 内容 | 关键词 |")
    out["architecture/README.md"] = _with_manual("\n".join(L), old.get("architecture/README.md"))
    # modules/README
    L = _fm("modules-README", "index") + [f"# 模块索引（{len(mods)} 个）", "", "> 全局依赖见 [../architecture/module-dependencies.md](../architecture/module-dependencies.md)；入口总览见 [../architecture/interfaces.md](../architecture/interfaces.md)。", ""]
    rows = []
    for p in mods:
        mm = next((x for x in scan["modules"] if x["id"] == p.module), {})
        rows.append(f"| {p.module} | [{Path(p.rel).name}]({Path(p.rel).name}) | {mm.get('files', '')} | {mm.get('entries', '')} | {mm.get('models', '')} | {_cell(p.summary)} |")
    L += _table(rows, "| 模块 | 文档 | 文件 | 入口 | 模型 | 职责摘要 |")
    out["modules/README.md"] = _with_manual("\n".join(L), old.get("modules/README.md"))
    # domains/README
    L = _fm("domains-README", "index") + [f"# 业务域索引（{len(doms)} 个）", "", "> 每个域：README.md（SDD：概述、流程图、分步说明、数据流转、异常、证据清单）+ 核心流程.md + 术语梳理.md + 配置清单.md。", ""]
    L += _table([f"| {p.name or p.domain} | [{p.domain}/README.md]({p.domain}/README.md) | {_cell(p.summary)} |" for p in doms], "| 域 | SDD | 一句话 |") if doms else ["（还没有业务域；在 plan.json 里加 `{\"id\": \"domain-<slug>\", \"kind\": \"domain\", \"name\": \"<名字>\", \"modules\": [...]}` 后重跑 plan / draft）", ""]
    out["domains/README.md"] = _with_manual("\n".join(L), old.get("domains/README.md"))
    return out


def _with_manual(text, old_text):
    if old_text:
        for _, block in manual_blocks(old_text):
            text += "\n" + block + "\n"
    return text


def _manifest_of(ctx, pages, scan):
    man = {"version": 1, "commit": head_sha(), "frozen_at": F.now(), "scan_generated_at": (scan or {}).get("generated_at"),
           "pages": {}}
    for p in pages:
        refs = sorted({r[0] for r in p.refs()})
        man["pages"][p.id] = {"path": p.rel, "hash": sha1(p.text), "sources": sorted(set(p.sources) | set(refs)),
                              "paths": p.paths, "protected": p.protected}
    return man


def cmd_freeze(ctx, a):
    ctx.need_init()
    pages = load_pages(ctx)
    if not pages:
        F.die("知识库里没有页面")
    errors, _ = lint_pages(ctx, pages)
    if errors:
        for x in errors:
            print(f"ERROR {x}")
        F.die("lint 有错误，先修好再 freeze")
    scan = need_scan(ctx)
    old = {}
    for rel in ("README.md", "architecture/README.md", "modules/README.md", "domains/README.md"):
        p = kb_dir(ctx) / rel
        if p.exists():
            old[rel] = p.read_text(encoding="utf-8")
    for rel, text in render_readmes(ctx, pages, scan, old).items():
        p = kb_dir(ctx) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    legacy = kb_dir(ctx) / "index.md"
    if legacy.exists():
        legacy.unlink()
    man = _manifest_of(ctx, pages, scan)
    dump(kb_dir(ctx) / "_manifest.json", man)
    print(f"已冻结 {len(pages)} 页 + 4 个 README，基线 {(man['commit'] or '')[:7] or '（无提交）'}，写入 _manifest.json")


def _changed_files(base):
    """返回 (修改过的文件, 新增或删除的文件)，含工作区未提交的改动。"""
    mod, ad = set(), set()
    if base and F.rev(base):
        mod |= {x for x in F.git("diff", "--name-only", "--diff-filter=M", base, "HEAD", check=False).split("\n") if x}
        ad |= {x for x in F.git("diff", "--name-only", "--diff-filter=ADR", base, "HEAD", check=False).split("\n") if x}
    for line in F.git("status", "--porcelain", "--untracked-files=all", check=False).splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ")[1]
        if path.startswith(".ai/"):
            continue
        if "M" in code:
            mod.add(path)
        if "?" in code or "A" in code or "D" in code or "R" in code:
            ad.add(path)
    return mod, ad


def compute_status(ctx):
    man = load_json(kb_dir(ctx) / "_manifest.json")
    if not man or not (kb_dir(ctx) / "README.md").exists():
        return None
    base_ok = bool(man.get("commit") and F.rev(man["commit"]))
    mod, ad = _changed_files(man.get("commit"))
    rows = []
    for pid, info in man["pages"].items():
        path = kb_dir(ctx) / info["path"]
        state, why = "新鲜", ""
        if not path.exists():
            state, why = "缺文件", info["path"]
        else:
            if sha1(path.read_text(encoding="utf-8", errors="replace")) != info["hash"]:
                state, why = "手改", "页内容与基线不同"
            if not base_ok:
                state, why = "过期", "基线提交不存在（历史被改写？）"
            hit = sorted(set(info["sources"]) & mod)
            if hit:
                state, why = "过期", f"{hit[0]} 已修改" + (f" 等 {len(hit)} 个" if len(hit) > 1 else "")
            else:
                for f in sorted(ad):
                    if f in info["sources"] or any(F.glob_match(f, g) for g in info.get("paths", [])):
                        state, why = "过期", f"{f} 新增/删除"
                        break
        rows.append({"id": pid, "path": info["path"], "state": state, "why": why, "protected": info.get("protected", False)})
    stale = [r for r in rows if r["state"] in ("过期", "缺文件")]
    return {"commit": man.get("commit"), "pages": rows, "stale": [r["id"] for r in stale],
            "edited": [r["id"] for r in rows if r["state"] == "手改"]}


def status_line(ctx):
    if not kb_dir(ctx).is_dir():
        return "知识库      缺失（运行 /flow-kb 生成）"
    st = compute_status(ctx)
    if st is None:
        return "知识库      未冻结（运行 /flow-kb 完成生成）"
    if st["stale"]:
        return f"知识库      {len(st['stale'])} 页过期，运行 /flow-kb 刷新"
    return f"知识库      新鲜（基线 {(st['commit'] or '')[:7]}）" + (f"，{len(st['edited'])} 页有手改" if st["edited"] else "")


def cmd_status(ctx, a):
    ctx.need_init()
    st = compute_status(ctx)
    if st is None:
        print("还没有知识库基线（缺 _manifest.json 或 README.md），运行 /flow-kb 生成")
        sys.exit(11)
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        print(f"基线 {(st['commit'] or '')[:7]}")
        print(f"{'页':<34}{'状态':<6}原因")
        for r in st["pages"]:
            print(f"{r['id']:<34}{r['state']:<6}{r['why']}")
        if st["stale"]:
            print(f"{len(st['stale'])} 页过期：运行 /flow-kb 刷新（先 flowctl kb scan，再对每页 flowctl kb draft --force --page <id>）")
        else:
            print("知识库与代码一致")
    sys.exit(10 if st["stale"] else 0)


# ---------------------------------------------------------------- recall

def recall_pages(ctx, text, paths, top=5):
    results = []
    for p in load_pages(ctx):
        hits = [t for t in p.triggers if F.trigger_hit(t, text)] if text else []
        phits = [g for g in p.paths if any(F.glob_match(x, g) for x in paths)] if paths else []
        src = [x for x in paths if x in p.sources] if paths else []
        mod_hit = bool(text and p.module and F.trigger_hit(p.module.split("/")[-1].split(".")[-1], text))
        pw = 2 if p.kind == "module" or p.kind.startswith("domain") else 1  # 总览页的 paths 覆盖全仓，按路径召回时让模块页排前面
        score = len(hits) + pw * len(phits) + 3 * (1 if src else 0) + (1 if mod_hit else 0)
        if score <= 0:
            continue
        why = []
        if hits:
            why.append("关键词 " + "/".join(hits))
        if mod_hit:
            why.append("模块名")
        if phits:
            why.append("路径 " + "/".join(phits))
        if src:
            why.append("引用文件 " + src[0])
        results.append({"id": p.id, "kind": p.kind, "module": p.module, "score": score, "path": f".ai/kb/{p.rel}",
                        "summary": p.summary, "why": why})
    results.sort(key=lambda r: (-r["score"], r["id"]))
    return results[:top]


def recall_text(results):
    if not results:
        return "没有召回到知识库页。\n"
    out = []
    for r in results:
        out.append(f"{r['score']:>3}  {r['kind']:<12} {r['id']}{'（' + r['module'] + '）' if r['module'] else ''}  ← {'；'.join(r['why'])}")
        out.append(f"       {r['summary']}")
        out.append(f"       {r['path']}")
    return "\n".join(out) + "\n"


def cmd_recall(ctx, a):
    ctx.need_init()
    text = a.text or ""
    for f in a.file or []:
        text += "\n" + read_text(f)
    paths = a.paths or []
    if not text.strip() and not paths:
        F.die("需要 --text、--file 或 --paths 中至少一个")
    res = recall_pages(ctx, text, paths, a.top or 5)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(recall_text(res), end="")


# ---------------------------------------------------------------- 入口

def dispatch(ctx, a):
    {"scan": cmd_scan, "plan": cmd_plan, "draft": cmd_draft, "facts": cmd_facts, "lint": cmd_lint,
     "freeze": cmd_freeze, "status": cmd_status, "recall": cmd_recall}[a.action](ctx, a)
