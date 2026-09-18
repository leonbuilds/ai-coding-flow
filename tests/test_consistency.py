"""文档与 skill 里写的命令必须真实存在：扫描 flowctl / wb 子命令和 flowctl 的长参数。"""
import re
import subprocess
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
FLOWCTL = KIT / "skills" / "flow" / "scripts" / "flowctl.py"
WB = KIT / "workbench" / "wb.py"
DOCS = [*KIT.glob("skills/*/SKILL.md"), *KIT.glob("agents/*.md"), *KIT.glob("docs/*.md"),
        *KIT.glob("playbooks/*.md"), *KIT.glob("rules/*.md"), KIT / "README.md", KIT / "方案与使用手册.md"]


def subcommands(script):
    out = subprocess.run(["python3", str(script), "--help"], capture_output=True, text=True).stdout
    m = re.search(r"\{([a-z,\-]+)\}", out)
    return set(m.group(1).split(",")) if m else set()


def help_text(script, *sub):
    return subprocess.run(["python3", str(script), *sub, "--help"], capture_output=True, text=True).stdout


class TestConsistency(unittest.TestCase):
    def test_flowctl_and_wb_commands_exist(self):
        subs = {"flowctl": subcommands(FLOWCTL), "wb": subcommands(WB)}
        self.assertIn("verify", subs["flowctl"])
        bad = []
        for p in DOCS:
            if not p.exists():
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for tool, sub in re.findall(r"\b(flowctl|wb)[\"`]?\s+([a-z][a-z-]+)", line):
                    if sub not in subs[tool]:
                        bad.append(f"{p.relative_to(KIT)}:{i}: {tool} {sub}")
        self.assertEqual(bad, [], "文档里引用了不存在的子命令")

    def test_flowctl_long_options_exist(self):
        bad = []
        cache = {}
        for p in DOCS:
            if not p.exists():
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for m in re.finditer(r"flowctl[\"`]?\s+((?:verify\s+)?[a-z]+)([^`|]*)", line):
                    sub = m.group(1).split()
                    opts = re.findall(r"(--[a-z][a-z-]+)", m.group(2))
                    if not opts:
                        continue
                    key = tuple(sub)
                    cache.setdefault(key, help_text(FLOWCTL, *sub))
                    bad += [f"{p.relative_to(KIT)}:{i}: flowctl {' '.join(sub)} {o}" for o in opts if o not in cache[key]]
        self.assertEqual(bad, [], "文档里引用了不存在的参数")

    def test_relative_markdown_links_resolve(self):
        bad = []
        for p in [*DOCS, KIT / "PLAN.md"]:
            if not p.exists():
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", line):
                    if "://" in target or target.startswith("mailto:"):
                        continue
                    if not (p.parent / target).exists():
                        bad.append(f"{p.relative_to(KIT)}:{i}: {target}")
        self.assertEqual(bad, [], "Markdown 相对链接指向不存在的文件")

    def test_skill_frontmatter(self):
        for p in KIT.glob("skills/*/SKILL.md"):
            head = p.read_text(encoding="utf-8").split("---")[1]
            self.assertIn(f"name: {p.parent.name}", head, p)
            self.assertRegex(head, r"description: .{20,}", p)


if __name__ == "__main__":
    unittest.main()
