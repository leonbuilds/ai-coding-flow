"""wb 的测试：CLAUDE_HOME / CODEX_HOME / AI_FLOW_HOME / CLAUDE_JSON 全部指向临时目录，绝不碰真实配置。"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
WB = KIT / "workbench" / "wb.py"
FAKES = KIT / "tests" / "fakes"


class TestWb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.t = Path(self.tmp.name)
        self.claude, self.codex, self.flow = t / "claude", t / "codex", t / "flow"
        self.claude.mkdir()
        self.codex.mkdir()
        (self.claude / "CLAUDE.md").write_text("# 已有内容\n不要动我\n", encoding="utf-8")
        self.claude_json = t / "claude.json"
        self.claude_json.write_text(json.dumps({"mcpServers": {
            "idea": {"type": "sse", "url": "http://127.0.0.1:1/sse"},
            "tool": {"type": "stdio", "command": "node", "args": ["s.js"], "env": {"TOKEN": "secret-123"}},
        }}))
        self.codex_mcp = t / "codex-mcp.json"
        self.codex_mcp.write_text(json.dumps([
            {"name": "repl", "transport": {"type": "stdio", "command": "node", "args": ["r.js"], "env": None}}]))
        self.env = dict(os.environ, CLAUDE_HOME=str(self.claude), CODEX_HOME=str(self.codex),
                        AI_FLOW_HOME=str(self.flow), CLAUDE_JSON=str(self.claude_json),
                        FAKE_CODEX_MCP=str(self.codex_mcp), FAKE_LOG=str(t / "calls.log"),
                        PATH=f"{FAKES}:{os.environ['PATH']}", WB_MCP_MANIFEST=str(t / "mcp.json"))
        self.manifest = t / "mcp.json"

    def tearDown(self):
        self.tmp.cleanup()

    def wb(self, *args, code=0, stdin=None):
        r = subprocess.run(["python3", str(WB), *args], env=self.env, capture_output=True, text=True, input=stdin)
        if code is not None and r.returncode != code:
            self.fail(f"wb {' '.join(args)} 退出码 {r.returncode}，期望 {code}\n{r.stdout}\n{r.stderr}")
        return r

    def test_sync_links_rules_and_is_idempotent(self):
        self.wb("sync")
        self.assertEqual(os.readlink(self.claude / "skills" / "flow"), str(KIT / "skills" / "flow"))
        self.assertTrue((self.codex / "skills" / "flow-review").is_symlink())
        self.assertTrue((self.claude / "agents" / "flow-verifier.md").is_symlink())
        self.assertFalse((self.codex / "agents").exists())          # Codex 没有原生 agents
        self.assertTrue((self.flow / "bin" / "flowctl").is_symlink())
        self.assertTrue((self.flow / "kit" / "playbooks").exists())
        self.assertTrue((self.flow / "specs" / "review").is_dir())
        md = (self.claude / "CLAUDE.md").read_text()
        self.assertTrue(md.startswith("# 已有内容\n不要动我\n"))   # 区块外内容原样保留
        self.assertIn("<!-- ai-coding-flow:begin sha=", md)
        self.assertIn("<!-- ai-coding-flow:begin sha=", (self.codex / "AGENTS.md").read_text())
        self.assertTrue(list((self.flow / "backups").glob("*/claude-CLAUDE.md")))
        again = self.wb("sync").stdout
        self.assertIn("已是最新", again)
        self.assertEqual(md, (self.claude / "CLAUDE.md").read_text())
        self.assertEqual(self.wb("doctor", code=None).stdout.count("✗ 规则区块"), 0)

    def test_doctor_detects_hand_edited_block(self):
        self.wb("sync")
        p = self.codex / "AGENTS.md"
        p.write_text(p.read_text().replace("## ", "## 手改 ", 1))
        out = self.wb("doctor", code=1).stdout
        self.assertIn("edited", out)

    def test_conflict_is_reported_not_overwritten(self):
        (self.claude / "skills" / "flow").mkdir(parents=True)
        (self.claude / "skills" / "flow" / "mine.txt").write_text("keep")
        r = self.wb("sync", code=1)
        self.assertIn("冲突", r.stderr)
        self.assertEqual((self.claude / "skills" / "flow" / "mine.txt").read_text(), "keep")

    def test_uninstall_removes_only_ours(self):
        (self.claude / "skills").mkdir()
        (self.claude / "skills" / "other").mkdir()
        self.wb("sync")
        self.wb("uninstall")
        self.assertFalse((self.claude / "skills" / "flow").exists())
        self.assertTrue((self.claude / "skills" / "other").exists())
        self.assertEqual((self.claude / "CLAUDE.md").read_text(), "# 已有内容\n不要动我\n")
        self.assertTrue((self.flow / "specs").exists())

    def test_mcp_import_redacts_and_diff_apply(self):
        self.wb("mcp", "import")
        m = json.loads(self.manifest.read_text())["servers"]
        self.assertEqual(m["tool"]["env"], {"TOKEN": "<from-host>"})   # 秘密不入库
        self.assertNotIn("secret-123", self.manifest.read_text())
        # 希望 tool、idea 两端都有
        m["tool"]["hosts"] = ["claude", "codex"]
        m["idea"]["hosts"] = ["claude", "codex"]
        del m["repl"]                                     # 从清单删掉 → 变成「清单外」，只提示不动它
        self.manifest.write_text(json.dumps({"servers": m}))
        out = self.wb("mcp", "diff", code=1).stdout
        self.assertIn("+ [codex] tool", out)
        self.assertIn("! [codex] idea", out)              # codex 不支持 sse
        self.assertIn("· [codex] repl", out)              # 清单外的只提示
        r = self.wb("mcp", "apply", "--yes")
        self.assertNotIn("secret-123", r.stdout)          # 打印时打码
        calls = (self.t / "calls.log").read_text()
        self.assertIn('"mcp", "add", "tool", "--env", "TOKEN=secret-123", "--", "node", "s.js"', calls)
        self.assertNotIn('"remove", "repl"', calls)
        self.assertNotIn('"add", "idea"', calls)          # 不支持的传输不会被硬写

    def test_same_name_different_config_is_not_overwritten(self):
        self.claude_json.write_text(json.dumps({"mcpServers": {
            "db": {"type": "stdio", "command": "node", "args": ["db.js"], "env": {"A": "1"}}}}))
        self.codex_mcp.write_text(json.dumps([{"name": "db", "transport": {
            "type": "stdio", "command": "python", "args": ["db.py"], "env": {"A": "1", "B": "2"}}}]))
        r = self.wb("mcp", "import")
        self.assertIn("配置不同", r.stderr)
        m = json.loads(self.manifest.read_text())["servers"]["db"]
        self.assertEqual(m["hosts"], ["claude"])
        self.wb("mcp", "apply", "--yes", "--allow-update")
        self.assertNotIn('"remove", "db"', (self.t / "calls.log").read_text() if (self.t / "calls.log").exists() else "")

    def test_extra_fields_are_never_silently_dropped(self):
        self.claude_json.write_text(json.dumps({"mcpServers": {
            "gh": {"type": "http", "url": "https://a/mcp", "headers": {"Authorization": "Bearer x"}}}}))
        self.wb("mcp", "import")
        m = json.loads(self.manifest.read_text())
        self.assertNotIn("Bearer x", self.manifest.read_text())
        m["servers"]["gh"]["hosts"] = ["claude", "codex"]
        m["servers"]["gh"]["url"] = "https://b/mcp"
        self.manifest.write_text(json.dumps(m))
        out = self.wb("mcp", "diff", code=1).stdout
        self.assertIn("! [codex] gh", out)             # 缺失但含 headers → 手动
        self.assertIn("! [claude] gh", out)            # 不一致且会丢 headers → 手动
        self.wb("mcp", "apply", "--yes", "--allow-update")
        log = self.t / "calls.log"
        self.assertNotIn('"gh"', log.read_text() if log.exists() else "")

    def test_update_needs_explicit_flag(self):
        self.claude_json.write_text(json.dumps({"mcpServers": {"u": {"type": "http", "url": "https://a"}}}))
        self.wb("mcp", "import")
        m = json.loads(self.manifest.read_text())
        m["servers"]["u"]["url"] = "https://b"
        self.manifest.write_text(json.dumps(m))
        self.wb("mcp", "apply", "--yes")
        log = self.t / "calls.log"
        self.assertNotIn('"remove", "-s", "user", "u"', log.read_text() if log.exists() else "")
        self.wb("mcp", "apply", "--yes", "--allow-update")
        self.assertIn('"remove", "-s", "user", "u"', log.read_text())

    def test_rules_block_keeps_crlf_and_refuses_damaged_markers(self):
        f = self.claude / "CLAUDE.md"
        f.write_bytes(b"line1\r\nline2\r\n")
        self.wb("sync", "--host", "claude")
        self.wb("uninstall", "--host", "claude")
        self.assertEqual(f.read_bytes(), b"line1\r\nline2\r\n")
        f.write_text("x\n<!-- ai-coding-flow:begin sha=deadbeef -->\nbroken\n", encoding="utf-8")
        r = self.wb("sync", "--host", "claude", code=None)
        self.assertIn("标记不完整", r.stderr)
        self.assertEqual(f.read_text().count("ai-coding-flow:begin"), 1)

    def test_mcp_apply_asks_and_can_skip(self):
        self.manifest.write_text(json.dumps({"servers": {
            "x": {"transport": "http", "url": "http://h/mcp", "hosts": ["codex"]}}}))
        r = self.wb("mcp", "apply", stdin="n\n")
        self.assertIn("已跳过", r.stdout)
        log = self.t / "calls.log"
        self.assertNotIn('"mcp", "add"', log.read_text() if log.exists() else "")


if __name__ == "__main__":
    unittest.main()
