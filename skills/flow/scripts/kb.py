"""kb：仓库代码知识库（.ai/kb/）的扫描、规划、草稿、校验、冻结、状态与召回。

只依赖 Python 3.9+ 标准库和 git。事实抽取覆盖 Java / Go / Python，其他语言只统计文件与 manifest。
所有抽取函数都是纯函数（文本 → 事实列表），便于直接单测。

  .ai/kb/scan.json        kb scan 的事实（每条带 file:line）
  .ai/kb/plan.json        页面规划（用户可编辑）
  .ai/kb/*.md、modules/   知识库页（frontmatter + 正文）
  .ai/kb/index.md         路由索引（kb freeze 生成）
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
FIXED_PAGES = ("overview", "architecture", "interfaces", "data-model", "glossary")
PAGE_KINDS = FIXED_PAGES + ("module",)
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
GO_EXT_RX = re.compile(r"(http\.NewRequest(?:WithContext)?\(|http\.(?:Get|Post)\(|grpc\.(?:Dial|NewClient)\(|resty\.New\(|\.NewClient\()")
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


GO_ROUTE_RX = re.compile(r"\b(\w+)\.(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|Any|Handle|HandleFunc|Get|Post|Put|Delete|Patch|Group)\s*\(\s*\"([^\"]*)\"")


def go_framework(text):
    for key, name in (("gin-gonic/gin", "gin"), ("labstack/echo", "echo"), ("go-chi/chi", "chi"),
                      ("gofiber/fiber", "fiber"), ("gorilla/mux", "gorilla"), ('"net/http"', "net/http")):
        if key in text:
            return name
    return None


def go_entries(rel, text):
    out, lines = [], text.splitlines()
    fw = go_framework(text)
    for i, line in enumerate(lines):
        s = line.strip()
        if re.match(r"^func\s+main\s*\(\s*\)", s):
            out.append({"kind": "main", "name": "main", "file": rel, "line": i + 1, "detail": {}})
            continue
        m = re.match(r"http\.Handle(Func)?\s*\(\s*\"([^\"]*)\"", s)
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
            out.append({"kind": "http", "name": f"{method} {m.group(3)}", "file": rel, "line": i + 1,
                        "detail": {"method": method, "path": m.group(3), "handler": hm.group(1) if hm else None,
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
        if re.search(r"cron\.New\(|\.AddFunc\(\s*\"|gocron\.", s):
            mm = re.search(r"AddFunc\(\s*\"([^\"]*)\"", s)
            out.append({"kind": "schedule", "name": mm.group(1) if mm else "cron", "file": rel, "line": i + 1, "detail": {}})
            continue
        if re.search(r"\.Subscribe\(|kafka\.NewReader\(|amqp\.Dial\(", s):
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


PY_ROUTE_RX = re.compile(r"@(\w+)\.(get|post|put|delete|patch|route|api_route|websocket)\s*\(\s*[\"']([^\"']*)")
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
        if f["module"] in modules:
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


def _module_page(m, scan):
    mid = m["id"]
    ents = [e for e in scan["entries"] if e["module"] == mid]
    mods = [x for x in scan["models"] if x["module"] == mid]
    first = [e["file"] for e in ents] + [x["file"] for x in mods]
    tests = [f for f in m["file_list"] if is_test_file(f)]
    rest = [f for f in m["file_list"] if f not in first and f not in tests]
    sources = list(dict.fromkeys(first + tests + rest))
    kinds = Counter(e["kind"] for e in ents)
    hints = []
    if ents:
        hints.append("入口 " + "、".join(f"{n} 个 {k}" for k, n in kinds.items()))
    if mods:
        hints.append("模型 " + "、".join(x["name"] for x in mods[:6]))
    deps = sorted({e["to"] for e in scan["edges"] if e["from"] == mid})
    if deps:
        hints.append("依赖 " + "、".join(deps))
    users = sorted({e["from"] for e in scan["edges"] if e["to"] == mid})
    if users:
        hints.append("被依赖 " + "、".join(users))
    safe = re.sub(r"[^\w.\-]+", "-", mid).strip("-")
    return {"id": f"module-{safe}", "kind": "module", "module": mid, "path": f"modules/{safe}.md",
            "goal": f"{m['name']} 模块：职责、入口、核心类型、依赖与关键调用链", "hints": hints,
            "sources": sources[:200], "sources_truncated": len(sources) > 200,
            "paths": [d + "/**" if d != "." else "*" for d in m["dirs"]], "status": "planned"}


def _fixed_pages(scan):
    man = [m["file"] for m in scan["build"]["manifests"]]
    cfg = [c["file"] for c in scan["configs"]]
    all_dirs = sorted({d for m in scan["modules"] for d in m["dirs"]})
    ent_files = list(dict.fromkeys(e["file"] for e in scan["entries"]))
    mod_files = list(dict.fromkeys(x["file"] for x in scan["models"]))

    def dirs_of(files):
        return sorted({(str(Path(f).parent) + "/**") if "/" in f else "*" for f in files})
    return [
        {"id": "overview", "kind": "overview", "path": "overview.md", "goal": "技术栈与版本、构建测试命令、目录地图",
         "sources": man + cfg, "paths": ["*"] + [d + "/**" for d in all_dirs if "/" not in d], "status": "planned"},
        {"id": "architecture", "kind": "architecture", "path": "architecture.md", "goal": "分层、依赖方向、模块依赖图、横切机制",
         "sources": man, "paths": [d + "/**" if d != "." else "*" for d in all_dirs], "status": "planned"},
        {"id": "interfaces", "kind": "interfaces", "path": "interfaces.md", "goal": "全部对外入口：HTTP / RPC / MQ / 定时 / CLI",
         "sources": ent_files, "paths": dirs_of(ent_files), "status": "planned"},
        {"id": "data-model", "kind": "data-model", "path": "data-model.md", "goal": "实体、表、DTO 与实体关系",
         "sources": mod_files, "paths": dirs_of(mod_files), "status": "planned"},
        {"id": "glossary", "kind": "glossary", "path": "glossary.md", "goal": "项目术语、缩写与不按字面理解的命名",
         "sources": [], "paths": [], "status": "planned"},
    ]


def cmd_plan(ctx, a):
    ctx.need_init()
    scan = need_scan(ctx)
    pp = kb_dir(ctx) / "plan.json"
    old = load_json(pp, {})
    old_pages = {p["id"]: p for p in old.get("pages", [])}
    fresh = _fixed_pages(scan) + [_module_page(m, scan) for m in scan["modules"] if m["files"] > 0]
    pages = []
    for p in fresh:
        o = old_pages.pop(p["id"], None)
        if o:
            if o.get("status") == "removed":
                pages.append(o)
                continue
            for k in ("goal", "hints", "paths", "status"):
                if k in o:
                    p[k] = o[k]
            if p.get("status") == "orphan":
                p["status"] = "planned"
        elif old_pages or old.get("pages"):
            p["status"] = "proposed"
        pages.append(p)
    for o in old_pages.values():  # scan 里不再出现的
        if o.get("status") != "removed":
            o["status"] = "orphan"
        pages.append(o)
    plan = {"version": 1, "tier": scan["tier"], "generated_at": F.now(),
            "scope": old.get("scope") or {"include": [], "exclude": []}, "notes": old.get("notes") or [], "pages": pages}
    dump(pp, plan)
    if a.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    ents = Counter(e["module"] for e in scan["entries"])
    mods = Counter(x["module"] for x in scan["models"])
    print(f"页面规划（{scan['tier']}）：写入 .ai/kb/plan.json")
    print(f"{'id':<28}{'kind':<13}{'module':<20}{'文件':>5}{'入口':>5}{'模型':>5}  {'status':<9} goal")
    for p in pages:
        n = len(p.get("sources", []))
        m = p.get("module", "")
        print(f"{p['id']:<28}{p['kind']:<13}{m:<20}{n:>5}{ents.get(m, len(scan['entries']) if p['kind'] == 'interfaces' else 0):>5}"
              f"{mods.get(m, len(scan['models']) if p['kind'] == 'data-model' else 0):>5}  {p['status']:<9} {p.get('goal', '')}")
    print("编辑 plan.json 后重跑 flowctl kb plan：改 scope 可缩小扫描范围；status 改 removed 可删页；proposed/orphan 需确认。")
    print("下一步：flowctl kb draft --all")


# ---------------------------------------------------------------- 命令：draft / facts

def _yaml_list(items):
    return "[" + ", ".join('"' + str(x).replace('"', "'") + '"' for x in items) + "]"


def load_plan(ctx):
    p = load_json(kb_dir(ctx) / "plan.json")
    if not p:
        F.die("还没有 plan.json，先运行 flowctl kb scan && flowctl kb plan")
    return p


def _triggers_for(page, scan):
    words = []
    if page["kind"] == "module":
        mid = page["module"]
        words += [page["module"].split("/")[-1].split(".")[-1]]
        for e in scan["entries"]:
            if e["module"] == mid and e["kind"] == "http":
                words += [w for w in re.split(r"[/{}:*]", e["detail"].get("path", "")) if len(w) >= 3 and not w.isdigit()]
        for x in scan["models"]:
            if x["module"] == mid:
                words.append(x["name"])
                if x["detail"].get("table"):
                    words.append(x["detail"]["table"])
    else:
        words = {"overview": ["构建", "技术栈", "build", "依赖", "目录"], "architecture": ["架构", "分层", "依赖", "模块"],
                 "interfaces": ["接口", "入口", "api", "endpoint", "路由", "消费", "定时"],
                 "data-model": ["表", "实体", "模型", "字段", "entity", "schema"], "glossary": ["术语", "缩写"]}[page["kind"]]
    seen, out = set(), []
    for w in words:
        if w and w.lower() not in seen and len(w) >= 2:
            seen.add(w.lower())
            out.append(w)
    return out[:8]


def render_template(kind, page, scan):
    tpl = F.TEMPLATE_DIR / "kb" / f"{kind}.md"
    if not tpl.exists():
        F.die(f"模板不存在：{tpl}")
    text = tpl.read_text(encoding="utf-8")
    fill = {"id": page["id"], "kind": kind, "module": page.get("module", ""), "goal": page.get("goal", ""),
            "hints": "；".join(page.get("hints", [])) or "（无）", "date": F.now()[:10],
            "triggers": _yaml_list(_triggers_for(page, scan)), "paths": _yaml_list(page.get("paths", [])),
            "sources": _yaml_list(page.get("sources", [])), "title": page.get("module") or kind}
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
    for s in skipped:
        print(f"跳过 {s}")
    if written:
        print("下一步：对每页执行 flowctl kb facts --page <id> 取事实，按模板写正文；写完 flowctl kb lint")


def page_facts(page, scan):
    kind, mid = page["kind"], page.get("module")
    if kind == "module":
        sel = {"entries": [e for e in scan["entries"] if e["module"] == mid],
               "models": [x for x in scan["models"] if x["module"] == mid],
               "externals": [x for x in scan["externals"] if x["module"] == mid],
               "configs": [c for c in scan["configs"] if c["module"] == mid],
               "edges_out": [e for e in scan["edges"] if e["from"] == mid],
               "edges_in": [e for e in scan["edges"] if e["to"] == mid],
               "module": next((m for m in scan["modules"] if m["id"] == mid), None)}
    elif kind == "interfaces":
        sel = {"entries": scan["entries"]}
    elif kind == "data-model":
        sel = {"models": scan["models"]}
    elif kind == "overview":
        sel = {"build": scan["build"], "configs": scan["configs"], "languages": scan["languages"], "stats": scan["stats"],
               "modules": [{k: m[k] for k in ("id", "lang", "files", "dirs", "layout")} for m in scan["modules"]],
               "tests": scan["tests"]}
    elif kind == "architecture":
        sel = {"modules": [{k: m[k] for k in ("id", "lang", "files", "dirs", "layers", "layout", "packages")} for m in scan["modules"]],
               "edges": scan["edges"], "cycles": scan["stats"]["cycles"], "externals": scan["externals"]}
    else:
        sel = {"models": scan["models"], "entries": scan["entries"][:50]}
    return sel


def facts_markdown(page, sel):
    lines = [f"# 事实：{page['id']}（来自 .ai/kb/scan.json，每条都带 file:line；没列出的就是 scan 没找到）"]
    m = sel.get("module")
    if m:
        lines.append(f"- 模块 {m['id']}：{m['files']} 个源文件，{m['tests']} 个测试文件，目录 {', '.join(m['dirs'])}，包 {', '.join(m['packages'][:5])}")
    for e in sel.get("entries", []):
        h = e["detail"].get("handler")
        lines.append(f"- [{KIND_LABEL.get(e['kind'], '证据-起点')}] {e['kind']} {e['name']} → {e['file']}:{e['line']}" + (f" ({h})" if h else "") + (f" [{e['module']}]" if "module" not in sel else ""))
    for x in sel.get("models", []):
        t = x["detail"].get("table")
        lines.append(f"- [证据-数据] {x['kind']} {x['name']}" + (f" 表 {t}" if t else "") + f" → {x['file']}:{x['line']}" + (f" [{x['module']}]" if "module" not in sel else ""))
    for x in sel.get("externals", []):
        lines.append(f"- [证据-调用] 外部 {x['name']} ×{x['detail']['count']} → {x['file']}:{x['line']}")
    for c in sel.get("configs", []):
        lines.append(f"- [证据-配置] {c['file']}（只列路径，不要把配置值写进文档）")
    for e in sel.get("edges_out", []):
        lines.append(f"- [证据-调用] 依赖 {e['to']}（import ×{e['count']}）")
    for e in sel.get("edges_in", []):
        lines.append(f"- [证据-调用] 被 {e['from']} 依赖（import ×{e['count']}）")
    if "edges" in sel:
        lines.append("- 模块依赖边（from → to ×count）：" + "; ".join(f"{e['from']} → {e['to']} ×{e['count']}" for e in sel["edges"]) if sel["edges"] else "- 未发现模块间 import")
        lines.append("- 循环依赖：" + ("; ".join(" -> ".join(c) for c in sel["cycles"]) if sel.get("cycles") else "未发现"))
    if "modules" in sel and "module" not in sel:
        for mm in sel["modules"]:
            lines.append(f"- 模块 {mm['id']}（{mm['lang']}，{mm['files']} 文件，{mm.get('layout')}）目录 {', '.join(mm['dirs'][:4])}")
    if "build" in sel:
        b = sel["build"]
        for man in b["manifests"]:
            fw = "，".join(f"{k} {v}" for k, v in man.get("frameworks", {}).items())
            lines.append(f"- [证据-配置] manifest {man['file']}:{man['line']} {man['ecosystem']} {man.get('name') or ''} {man.get('version') or ''} {fw}")
            for d in man["dependencies"][:40]:
                lines.append(f"  - 依赖 {d.get('group', '') + ':' if d.get('group') else ''}{d['artifact']} {d.get('version') or '（版本未写）'}{'' if d.get('resolved', True) else '（未解析）'} → {man['file']}:{d['line']}")
        for k, v in b["commands"].items():
            if v:
                lines.append(f"- 命令 {k}：`{v['cmd']}`（猜测：{v['why']}；必须实际跑过才能写「已验证」）")
        lines.append(f"- 语言统计：{json.dumps(sel['languages'], ensure_ascii=False)}；测试：{json.dumps(sel['tests'], ensure_ascii=False)}")
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
        self.id = str(self.meta.get("id") or path.stem)
        self.kind = str(self.meta.get("kind") or "")
        self.module = str(self.meta.get("module") or "")
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


def load_pages(ctx):
    root = kb_dir(ctx)
    if not root.is_dir():
        return []
    return [Page(p, root) for p in sorted(root.rglob("*.md")) if not p.name.startswith("_") and p.name != "index.md"]


def _line_count_cache():
    cache = {}

    def count(p):
        if p not in cache:
            try:
                cache[p] = read_text(p).count("\n") + (0 if read_text(p).endswith("\n") else 1)
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
        want = f"module-{p.path.stem}" if p.kind == "module" else p.path.stem
        if p.id != want:
            e(f"id「{p.id}」与文件名不一致（应为 {want}）")
        if ids[p.id] > 1:
            e(f"id 重复：{p.id}")
        if str(p.meta.get("type") or "") != "kb":
            e("缺少 type: kb")
        if p.kind not in PAGE_KINDS:
            e(f"kind 不合法：{p.kind or '（空）'}")
        if not p.summary or TODO in p.summary:
            e("summary 未填写（index.md 靠它生成）")
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
        for s in p.sources:
            if s not in tracked and not (ctx.root / s).exists():
                e(f"sources 里的文件不存在：{s}")
        for path, a1, a2 in p.refs():
            full = ctx.root / path
            if path not in tracked and not full.exists():
                e(f"引用了不存在的文件 {path}:{a1}")
                continue
            n = count(full)
            if n is not None and (a1 > n or (a2 and a2 > n)):
                e(f"{path}:{a1}{'-' + str(a2) if a2 else ''} 超出文件行数 {n}")
        for m in re.finditer(r"```[^\n]*\n(.*?)```", p.body, re.S):
            if m.group(1).count("\n") > 12:
                w(f"代码块超过 12 行（{m.group(1).count(chr(10))} 行），知识库只放引用不放实现")
        if len(p.body) > 12000:
            w(f"正文 {len(p.body)} 字符，建议拆页")
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
        F.die("知识库里没有页面（.ai/kb/*.md）")
    errors, warns = lint_pages(ctx, pages)
    for x in errors:
        print(f"ERROR {x}")
    for x in warns:
        print(f"WARN  {x}")
    print(f"共 {len(pages)} 页：{len(errors)} 个错误，{len(warns)} 个警告")
    if errors:
        sys.exit(1)


# ---------------------------------------------------------------- freeze / status

def render_index(ctx, pages, old_text=None):
    lines = ["---", "id: index", "type: kb", "kind: index", f"updated: {F.now()[:10]}", "---", "",
             "# 代码知识库索引", "",
             "<!-- 由 flowctl kb freeze 从各页 frontmatter 生成，勿手改正文；人工内容放到末尾的 kb:manual 块里。-->",
             "<!-- 用法：先按 triggers / paths 找到相关页（或执行 flowctl kb recall），只读命中的页；页里的 file:line 仍要打开核对。-->", ""]
    order = ["overview", "architecture", "module", "interfaces", "data-model", "glossary"]
    label = {"overview": "概览", "architecture": "架构", "module": "模块", "interfaces": "对外入口", "data-model": "数据模型", "glossary": "术语"}
    for kind in order:
        group = [p for p in pages if p.kind == kind]
        if not group:
            continue
        lines += [f"## {label[kind]}", "", "| 页 | 一句话 | triggers | paths |", "|---|---|---|---|"]
        for p in sorted(group, key=lambda x: x.id):
            lines.append(f"| [{p.id}]({p.rel}) | {p.summary.replace('|', '/')} | {', '.join(p.triggers)} | {', '.join(p.paths)} |")
        lines.append("")
    text = "\n".join(lines)
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
    idx = kb_dir(ctx) / "index.md"
    idx.write_text(render_index(ctx, pages, idx.read_text(encoding="utf-8") if idx.exists() else None), encoding="utf-8")
    man = _manifest_of(ctx, pages, load_json(kb_dir(ctx) / "scan.json"))
    dump(kb_dir(ctx) / "_manifest.json", man)
    print(f"已冻结 {len(pages)} 页，基线 {(man['commit'] or '')[:7] or '（无提交）'}，写入 index.md 与 _manifest.json")


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
    if not man or not (kb_dir(ctx) / "index.md").exists():
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
        print("还没有知识库基线（缺 _manifest.json 或 index.md），运行 /flow-kb 生成")
        sys.exit(11)
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        print(f"基线 {(st['commit'] or '')[:7]}")
        print(f"{'页':<28}{'状态':<6}原因")
        for r in st["pages"]:
            print(f"{r['id']:<28}{r['state']:<6}{r['why']}")
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
        score = len(hits) + 2 * len(phits) + 3 * (1 if src else 0) + (1 if mod_hit else 0)
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
