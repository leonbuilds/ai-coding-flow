"""flowctl 的端到端测试：每个用例在临时 HOME 和临时 git 仓库里跑，外部 CLI 用 tests/fakes 里的假实现。

运行：python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
FLOWCTL = KIT / "skills" / "flow" / "scripts" / "flowctl.py"
FAKES = KIT / "tests" / "fakes"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.home = self.t / "home"
        self.home.mkdir()
        self.repo = self.t / "repo"
        self.repo.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PATH=f"{FAKES}:{os.environ['PATH']}",
                        FAKE_LOG=str(self.t / "calls.log"), GIT_CONFIG_GLOBAL=str(self.t / "gitconfig"))
        for k in ("FAKE_VERDICT", "FAKE_TOUCH", "FAKE_ADD", "FAKE_EXIT", "FAKE_PROMPT_LOG", "AI_FLOW_HOME", "CODEX_HOME"):
            self.env.pop(k, None)
        self.git("init", "-q")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@t")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True,
                              check=check).stdout

    def f(self, *args, ok=True, cwd=None, **env):
        e = dict(self.env, **env)
        r = subprocess.run(["python3", str(FLOWCTL), *args], cwd=cwd or self.repo, env=e,
                           capture_output=True, text=True)
        if ok and r.returncode != 0:
            self.fail(f"flowctl {' '.join(args)} 失败：{r.stderr}\n{r.stdout}")
        return r

    def write(self, rel, text, mode="w", encoding="utf-8"):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding=encoding)

    def commit(self, msg="c"):
        self.git("add", "-A", ".", ":!.ai")
        self.git("commit", "-qm", msg)

    def change_dir(self):
        name = (self.repo / ".ai" / "current").read_text().strip()
        return self.repo / ".ai" / "changes" / name

    def metrics(self, *extra):
        self.f("metrics", "--json", *extra)
        return json.loads((self.change_dir() / "metrics.json").read_text())

    def basic_change(self):
        """T1：v1 写 5 行，final 改 2 行、删 3 行；提交。"""
        self.write("app.py", "def a():\n    return 1\n")
        self.commit("init")
        self.f("init")
        self.f("new", "export-orders", "--title", "订单导出")
        self.f("snapshot", "T1", "base")
        self.write("app.py", "def a():\n    return 1\n\ndef b():\n    x = 2\n    y = 3\n    return x + y\n")
        self.write("new.py", "new file line\n")
        self.f("snapshot", "T1", "v1")
        self.write("app.py", "def a():\n    return 1\n\ndef b():\n    x = 20\n    return x\n")
        self.f("snapshot", "T1", "final")
        self.commit("t1")


class TestMetrics(Base):
    def test_fpy_and_ai_ratio(self):
        self.basic_change()
        m = self.metrics()
        t1 = m["tasks"]["T1"]
        self.assertEqual((t1["v1_lines"], t1["final_lines"], t1["rewritten_lines"], t1["discarded_lines"]),
                         (5, 4, 2, 3))
        self.assertAlmostEqual(t1["fpy"], 0.5)
        self.assertEqual((m["ai_ratio"]["ai_lines"], m["ai_ratio"]["total_added"]), (2, 4))
        self.assertEqual(len(m["commits"]), 1)
        self.assertEqual(m["files"], ["app.py", "new.py"])

    def test_snapshots_do_not_touch_index_or_branch(self):
        self.write("a.txt", "1\n")
        self.commit()
        self.f("init")
        self.f("new", "idx")
        self.write("a.txt", "2\n")
        self.git("add", "a.txt")
        self.write("a.txt", "3\n")
        before = (self.git("write-tree").strip(), self.git("rev-parse", "HEAD").strip())
        self.f("snapshot", "T1", "base")
        self.assertEqual(before, (self.git("write-tree").strip(), self.git("rev-parse", "HEAD").strip()))

    def test_unicode_plusplus_gbk_spaces_noprefix_and_task_order(self):
        self.git("config", "diff.noprefix", "true")
        self.write("seed.txt", "seed\n")
        self.commit()
        self.f("init")
        self.f("new", "edge-cases")
        self.f("snapshot", "T2", "base")  # 先做 T2
        self.write("文件.py", "a\nb\nc\n")
        self.write("a.c", "x1\n++ y\nx3\nx4\n")
        self.write("Gbk.java", "中文\nok\n".encode("gbk"))
        self.write("sp ace.txt", "p\n")
        self.f("snapshot", "T2", "v1")
        self.f("snapshot", "T2", "final")
        self.commit()
        self.f("snapshot", "T1", "base")
        self.write("z.py", "z\n")
        self.f("snapshot", "T1", "v1")
        self.write("z.py", "z\nw\n")
        self.f("snapshot", "T1", "final")
        self.commit()
        m = self.metrics()
        self.assertEqual(m["tasks"]["T2"]["final_lines"], 10)
        self.assertEqual(m["tasks"]["T1"]["fpy"], 0.5)
        self.assertEqual((m["ai_ratio"]["ai_lines"], m["ai_ratio"]["total_added"]), (11, 12))

    def test_empty_repo_still_writes_fpy(self):
        self.f("init")
        self.f("new", "empty-repo")
        self.f("snapshot", "T1", "base")
        self.write("q.py", "q\nr\n")
        self.f("snapshot", "T1", "v1")
        self.f("snapshot", "T1", "final")
        m = self.metrics()
        self.assertEqual(m["fpy_weighted"], 1.0)
        self.assertNotIn("ai_ratio", m)

    def test_uncommitted_falls_back_to_final_snapshot(self):
        self.write("s", "s\n")
        self.commit()
        self.f("init")
        self.f("new", "uncommitted")
        self.f("snapshot", "T1", "base")
        self.write("k.py", "k\nl\n")
        self.f("snapshot", "T1", "v1")
        self.f("snapshot", "T1", "final")
        m = self.metrics()
        self.assertIn("final 快照", m["ai_ratio"]["to"])
        self.assertEqual(m["ai_ratio"]["ai_lines"], 2)
        self.assertNotIn("commits", m)


class TestSpecs(Base):
    def setUp(self):
        super().setUp()
        self.write("x", "x\n")
        self.commit()
        self.f("init")
        self.gspecs = self.home / ".ai-flow" / "specs" / "dev"
        self.gspecs.mkdir(parents=True)

    def spec(self, base, rel, fm, body="正文\n"):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\n" + fm + "\n---\n" + body, encoding="utf-8")

    def test_recall_triggers_paths_always_and_override(self):
        s = self.repo / ".ai" / "specs"
        self.spec(s, "dev/money.md", 'id: dev-money\ntriggers: [金额, amount]\npaths: ["src/**/billing/**"]\nwhen: 改金额时')
        self.spec(s, "review/g.md", "id: review-g\nwhen: 每次\nalways: yes")
        self.spec(self.gspecs, "dup.md", "id: dev-money\ntriggers: [金额]\nwhen: 全局版")
        self.spec(self.gspecs, "t.md", "id: dev-test\ntriggers: [单元测试]\nwhen: 写测试时")
        r = json.loads(self.f("recall", "--text", "调整 amount 与单元测试", "--paths", "src/billing/a.py",
                              "--json").stdout)
        ids = [x["id"] for x in r]
        self.assertEqual(ids[0], "dev-money")          # 关键词 + 路径，分数最高
        self.assertIn("dev-test", ids)
        self.assertNotIn("review-g", ids)              # 常驻检查项不参与召回
        self.assertEqual([x["scope"] for x in r if x["id"] == "dev-money"], ["repo"])  # 仓库级覆盖全局
        self.assertEqual(self.f("specs", "lint").returncode, 0)

    def test_ascii_trigger_is_whole_word(self):
        self.spec(self.repo / ".ai" / "specs", "dev/id.md", "id: dev-order\ntriggers: [order]\nwhen: x")
        r = json.loads(self.f("recall", "--text", "reorder the list", "--json").stdout)
        self.assertEqual(r, [])

    def test_lint_rejects_spec_without_when_or_triggers(self):
        self.spec(self.repo / ".ai" / "specs", "tech/bad.md", "id: tech-bad")
        r = self.f("specs", "lint", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("缺少 when", r.stdout)

    def test_scores_flag_revision(self):
        self.spec(self.repo / ".ai" / "specs", "dev/a.md", "id: dev-a\ntriggers: [aa]\nwhen: x")
        for v in ("mislead", "hit", "mislead"):
            self.f("score", "dev-a", v)
        self.assertIn("误导", self.f("specs", "report").stdout)


class TestVerify(Base):
    def setUp(self):
        super().setUp()
        self.basic_change()
        (self.change_dir() / "tasks.md").write_text("- [x] T1 x\n  - 验收：python3 -c 'print(1)'\n", encoding="utf-8")

    def start(self, *extra, ok=True, **env):
        return self.f("verify", "start", "--host", "claude", "--host-model", "opus", "--engine", "codex",
                      "--model", "gpt-x", "--reasoning", "high", *extra, ok=ok, **env)

    def test_cross_vendor_pass_is_recorded(self):
        log = self.t / "prompt.log"
        r = self.start(FAKE_PROMPT_LOG=str(log))
        self.assertIn("PASS", r.stdout)
        meta = json.loads((self.change_dir() / "review" / "round-1.json").read_text())
        self.assertEqual((meta["level"], meta["verdict"], meta["engine"]), ("L3", "PASS", "codex"))
        self.assertIn("| 1 | PASS | codex / gpt-x / high | L3 |", (self.change_dir() / "review.md").read_text())
        prompt = log.read_text()
        self.assertIn("独立的代码审查者", prompt)          # 角色说明来自 agents/flow-verifier.md
        self.assertIn("python3 -c 'print(1)'", prompt)     # 验收命令
        self.assertNotIn("name: flow-verifier", prompt)    # frontmatter 已去掉
        calls = (self.t / "calls.log").read_text()
        self.assertIn('"-m", "gpt-x"', calls)
        self.assertIn('model_reasoning_effort=\\"high\\"', calls)
        last = json.loads((self.home / ".ai-flow" / "last_models.json").read_text())
        self.assertEqual(last["verifier"]["model"], "gpt-x")

    def test_reject_then_second_round_sees_previous_findings(self):
        log = self.t / "prompt.log"
        self.start(FAKE_VERDICT="结论：REJECT\n\n| 1 | 阻断 | 幻觉 | app.py:5 | 独特标记XYZ | 证据 |\n")
        self.start(FAKE_PROMPT_LOG=str(log))
        self.assertIn("独特标记XYZ", log.read_text())
        rounds = sorted(p.name for p in (self.change_dir() / "review").glob("round-*.json"))
        self.assertEqual(rounds, ["round-1.json", "round-2.json"])
        events = [json.loads(l)["event"] for l in (self.change_dir() / "trace.jsonl").read_text().splitlines()
                  if json.loads(l)["stage"] == "review" and json.loads(l)["event"] != "note"]
        self.assertEqual(events, ["rejected", "accepted"])

    def test_reviewer_touching_worktree_invalidates_round(self):
        r = self.start(ok=False, FAKE_TOUCH="app.py")
        self.assertEqual(r.returncode, 2)
        meta = json.loads((self.change_dir() / "review" / "round-1.json").read_text())
        self.assertEqual(meta["verdict"], "INVALID")

    def test_missing_verdict_is_invalid(self):
        r = self.start(ok=False, FAKE_VERDICT="我觉得挺好\n")
        self.assertEqual(r.returncode, 2)

    def test_verdict_must_be_a_single_unambiguous_line(self):
        r = self.start(FAKE_VERDICT="如果验收都通过，结论：PASS。但验收失败了。\n结论：REJECT\n")
        self.assertIn("REJECT", r.stdout)
        r = self.start(ok=False, FAKE_VERDICT="结论：PASS | REJECT\n")
        self.assertEqual(r.returncode, 2)
        r = self.start(ok=False, FAKE_VERDICT="结论：PASS\n...\n结论：REJECT\n")
        self.assertEqual(r.returncode, 2)
        r = self.start(FAKE_VERDICT="**结论：** **PASS**\n")
        self.assertIn("PASS", r.stdout)

    def test_finished_round_cannot_be_recorded_again(self):
        self.start(ok=False, FAKE_TOUCH="app.py")               # INVALID
        self.git("checkout", "--", "app.py")
        r = self.f("verify", "record", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("已记录为 INVALID", r.stderr)

    def test_new_untracked_file_is_a_warning_not_invalid(self):
        r = self.start(FAKE_ADD="cache.pyc")
        self.assertIn("PASS", r.stdout)
        meta = json.loads((self.change_dir() / "review" / "round-1.json").read_text())
        self.assertIn("cache.pyc", meta["warning"])

    def test_native_claude_path(self):
        r = self.f("verify", "start", "--host", "claude", "--host-model", "opus", "--engine", "claude",
                   "--model", "fable")
        info = json.loads(r.stdout)
        self.assertEqual((info["mode"], info["level"]), ("native", "L2"))
        Path(info["out_file"]).write_text("结论：PASS\n", encoding="utf-8")
        self.assertIn("PASS", self.f("verify", "record").stdout)

    def test_codex_host_calls_claude_cli(self):
        r = self.f("verify", "start", "--host", "codex", "--engine", "claude", "--model", "opus")
        self.assertIn("PASS", r.stdout)
        calls = (self.t / "calls.log").read_text()
        self.assertIn('"--agent", "flow-verifier"', calls)
        self.assertIn('"--model", "opus"', calls)


class TestScoutAndSquash(Base):
    def test_codex_scout_gets_recall_written_for_it(self):
        self.write("x", "x\n")
        self.commit()
        self.f("init")
        s = self.repo / ".ai" / "specs" / "domain" / "m.md"
        s.write_text("---\nid: domain-m\ntriggers: [导出]\nwhen: x\n---\nbody\n", encoding="utf-8")
        self.f("new", "scout-case")
        (self.change_dir() / "proposal.md").write_text("需求：订单导出\n", encoding="utf-8")
        r = self.f("agent", "scout", "--host", "codex", "--engine", "codex", "--model", "m1",
                   "--input", str(self.change_dir() / "proposal.md"))
        self.assertEqual(json.loads(r.stdout[r.stdout.index("{"):])["mode"], "external")
        rec = json.loads((self.change_dir() / "recalled.json").read_text())
        self.assertEqual([x["id"] for x in rec], ["domain-m"])
        self.assertIn('"-s", "read-only"', (self.t / "calls.log").read_text())

    def test_squash_counts_only_the_squash_commit(self):
        self.write("app.py", "a\n")
        self.commit()
        main = self.git("rev-parse", "HEAD").strip()
        self.f("init")
        self.f("new", "squash-case")
        self.f("snapshot", "T1", "base")
        self.write("feat.py", "f1\nf2\n")
        self.f("snapshot", "T1", "v1")
        self.f("snapshot", "T1", "final")
        self.git("stash", "-u")
        self.write("other.py", "someone else\n")                 # 别人在主干上的提交
        self.commit("other")
        self.git("stash", "pop")
        self.commit("squash: feature")
        sq = self.git("rev-parse", "HEAD").strip()
        m = self.metrics("--to", sq, "--squash")
        self.assertEqual((m["ai_ratio"]["ai_lines"], m["ai_ratio"]["total_added"]), (2, 2))
        self.assertEqual(m["commits"], [sq])
        self.assertNotIn("other.py", m["files"])
        self.assertNotEqual(main, sq)


class TestLoop(Base):
    def test_locate_from_subdirectory(self):
        self.basic_change()
        self.metrics()
        (self.repo / "sub").mkdir()
        out = self.f("locate", "../new.py:1", cwd=self.repo / "sub").stdout
        self.assertIn("export-orders", out)

    def test_aftercare_and_locate(self):
        self.basic_change()
        self.metrics()
        self.f("close")
        # 合入后别人改掉了 AI 写的 "def b():"
        self.write("app.py", "def a():\n    return 1\n\ndef bb():\n    x = 20\n    return x\n")
        self.commit("later")
        out = self.f("aftercare", (self.repo / ".ai" / "changes").iterdir().__next__().name).stdout
        self.assertIn("1/2", out)                 # AI 行：def b(): 与 new file line，剩 1 行
        self.assertIn("1 个", out)                # 关闭后有 1 个提交改了这批文件
        loc = self.f("locate", "new.py:1").stdout
        self.assertIn("export-orders", loc)
        self.assertIn("引入这一行的提交属于该变更", loc)

    def test_incident_and_local_only_init(self):
        self.write("x", "x\n")
        self.commit()
        self.f("init", "--local-only")
        self.assertIn(".ai/", (self.repo / ".git" / "info" / "exclude").read_text())
        self.f("incident", "new", "npe-on-export", "--title", "导出空指针")
        files = list((self.repo / ".ai" / "incidents").glob("*-npe-on-export.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("导出空指针", files[0].read_text())
        self.assertEqual(self.git("status", "--porcelain").strip(), "")  # .ai/ 被忽略

    def test_models_outside_repo(self):
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text('model = "gpt-sol"\n[x]\nmodel = "no"\n')
        (self.home / ".codex" / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-a", "visibility": "list", "description": "A",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
            {"slug": "hidden", "visibility": "hide"}]}))
        info = json.loads(self.f("models", "verifier", "--json", cwd=self.t).stdout)
        self.assertEqual([m["model"] for m in info["codex"]], ["gpt-sol", "gpt-a"])
        self.assertEqual(info["codex"][1]["reasoning"], ["low", "high"])
        self.assertTrue(info["cli_available"]["codex"])


if __name__ == "__main__":
    unittest.main()
