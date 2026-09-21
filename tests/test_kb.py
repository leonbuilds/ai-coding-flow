"""flowctl kb 的端到端测试：临时 git 仓库里放 Java / Go / Python 迷你工程，跑 scan → plan → draft → lint → freeze → status → recall。

运行：python3 -m unittest tests.test_kb -v
"""
import json
import re
import unittest

from tests.test_flowctl import Base

POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.1</version>
  </parent>
  <artifactId>shop</artifactId>
  <version>1.0.0</version>
  <properties>
    <spring.version>3.2.1</spring.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework.kafka</groupId>
      <artifactId>spring-kafka</artifactId>
      <version>${spring.version}</version>
    </dependency>
    <dependency>
      <groupId>com.acme</groupId>
      <artifactId>mystery</artifactId>
      <version>${unknown.version}</version>
    </dependency>
  </dependencies>
</project>
"""
JAVA_BASE = "src/main/java/com/acme/shop/"


class SpringRepo(Base):
    def setUp(self):
        super().setUp()
        self.write("pom.xml", POM)
        self.write("mvnw", "#!/bin/sh\n")
        self.write(JAVA_BASE + "order/OrderController.java", """package com.acme.shop.order;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class OrderController {
    @GetMapping("/orders/{id}")
    public OrderEntity getOrder(@PathVariable long id) { return null; }

    @RequestMapping(value = "/orders",
                    method = RequestMethod.POST)
    public void create() {}
}
""")
        self.write(JAVA_BASE + "order/OrderEntity.java", """package com.acme.shop.order;

import jakarta.persistence.*;

@Entity
@Table(name = "t_order")
public class OrderEntity { private long id; }
""")
        self.write(JAVA_BASE + "order/OrderJob.java", """package com.acme.shop.order;

public class OrderJob {
    @Scheduled(cron = "0 * * * * ?")
    public void sweep() {}
}
""")
        self.write(JAVA_BASE + "order/OrderConsumer.java", """package com.acme.shop.order;

public class OrderConsumer {
    @KafkaListener(topics = "order-created", groupId = "shop")
    public void onCreated(String msg) {}
}
""")
        self.write(JAVA_BASE + "user/UserService.java", """package com.acme.shop.user;

import com.acme.shop.order.OrderEntity;
import org.springframework.web.client.RestTemplate;

public class UserService {
    private RestTemplate rt = new RestTemplate();
}
""")
        self.write(JAVA_BASE + "user/Gbk.java", "package com.acme.shop.user;\n// 中文注释\n@Service\npublic class Gbk {}\n".encode("gbk"))
        self.write("src/test/java/com/acme/shop/order/OrderControllerTest.java",
                   "package com.acme.shop.order;\nimport org.junit.jupiter.api.Test;\nclass OrderControllerTest {}\n")
        self.write("src/main/resources/application.yml", "spring:\n  datasource:\n    password: secret\n")
        self.commit("spring")
        self.f("init")

    def scan(self):
        self.f("kb", "scan")
        return json.loads((self.repo / ".ai" / "kb" / "scan.json").read_text())

    def test_scan_facts(self):
        s = self.scan()
        self.assertEqual(s["tier"], "flat")
        self.assertEqual(s["build"]["ecosystems"], ["maven"])
        man = s["build"]["manifests"][0]
        self.assertEqual(man["frameworks"]["spring_boot"], "3.2.1")
        deps = {d["artifact"]: d for d in man["dependencies"]}
        self.assertEqual(deps["spring-kafka"]["version"], "3.2.1")
        self.assertTrue(deps["spring-kafka"]["resolved"])
        self.assertFalse(deps["mystery"]["resolved"])
        self.assertEqual(s["build"]["commands"]["test"]["cmd"], "./mvnw test")
        self.assertTrue(s["build"]["commands"]["test"]["guessed"])
        ents = {e["name"]: e for e in s["entries"]}
        self.assertIn("GET /api/orders/{id}", ents)
        e = ents["GET /api/orders/{id}"]
        self.assertEqual((e["file"], e["line"], e["module"]), (JAVA_BASE + "order/OrderController.java", 8, "order"))
        self.assertEqual(e["detail"]["handler"], "getOrder")
        self.assertIn("POST /api/orders", ents)
        kinds = {e["kind"] for e in s["entries"]}
        self.assertTrue({"http", "schedule", "mq"} <= kinds)
        self.assertEqual(ents["order-created"]["detail"]["topics"], ["order-created"])
        models = {m["name"]: m for m in s["models"]}
        self.assertEqual(models["OrderEntity"]["detail"]["table"], "t_order")
        self.assertEqual({m["id"] for m in s["modules"]}, {"order", "user"})
        self.assertEqual(s["edges"], [{"from": "user", "to": "order", "count": 1}])
        self.assertEqual([x["name"] for x in s["externals"]], ["RestTemplate"])
        self.assertEqual(s["configs"][0]["file"], "src/main/resources/application.yml")
        self.assertEqual(s["tests"]["by_module"], {"order": 1})
        self.assertEqual(s["languages"]["java"]["files"], 6)

    def test_plan_idempotent(self):
        self.scan()
        self.f("kb", "plan")
        pp = self.repo / ".ai" / "kb" / "plan.json"
        plan = json.loads(pp.read_text())
        ids = [p["id"] for p in plan["pages"]]
        # order 有 4 个入口 + 1 个模型 → 单独开页；user 没有入口 → 并入 modules.md
        self.assertEqual(ids, ["overview", "architecture", "modules", "module-order"])
        by0 = {p["id"]: p for p in plan["pages"]}
        self.assertEqual(by0["modules"]["modules"], ["user"])
        self.assertEqual(plan["limits"]["max_module_pages"], 6)
        self.assertLessEqual(len(by0["module-order"]["sources"]), 30)
        self.assertTrue(all(p["status"] == "planned" for p in plan["pages"]))
        plan["notes"].append("只关注 order")
        plan["scope"]["exclude"] = ["**/Gbk.java"]
        for p in plan["pages"]:
            if p["id"] == "module-order":
                p["goal"] = "自定义目标"
        plan["limits"]["min_entries"] = 1  # 门槛降到 1：pay 的一个 HTTP 入口也够开页
        plan["pages"].append({"id": "module-user", "kind": "module", "status": "planned"})  # 用户强制给 user 开页
        pp.write_text(json.dumps(plan, ensure_ascii=False))
        self.write(JAVA_BASE + "pay/PayController.java", "package com.acme.shop.pay;\n@RestController\npublic class PayController {\n    @PostMapping(\"/pay\")\n    public void pay() {}\n}\n")
        self.commit("pay")
        self.scan()
        self.f("kb", "plan")
        plan = json.loads(pp.read_text())
        by = {p["id"]: p for p in plan["pages"]}
        self.assertEqual(plan["notes"], ["只关注 order"])
        self.assertEqual(plan["scope"]["exclude"], ["**/Gbk.java"])
        self.assertEqual(by["module-order"]["goal"], "自定义目标")
        self.assertEqual(by["module-pay"]["status"], "proposed")
        self.assertEqual(by["module-user"]["status"], "planned")
        self.assertEqual(by["module-user"]["path"], "modules/user.md")
        self.assertEqual(by["modules"]["modules"], [])
        by["module-user"]["status"] = "removed"
        pp.write_text(json.dumps(plan, ensure_ascii=False))
        self.f("kb", "plan")
        by = {p["id"]: p for p in json.loads(pp.read_text())["pages"]}
        self.assertEqual(by["module-user"]["status"], "removed")
        self.assertEqual(by["modules"]["modules"], ["user"])
        s = json.loads((self.repo / ".ai" / "kb" / "scan.json").read_text())
        self.assertNotIn(JAVA_BASE + "user/Gbk.java", [f for m in s["modules"] for f in m["file_list"]])

    def fill_pages(self):
        """把所有草稿填成能过 lint 的最小内容。"""
        kb = self.repo / ".ai" / "kb"
        for p in list(kb.glob("*.md")) + list(kb.glob("modules/*.md")):
            if p.name == "index.md":
                continue
            t = p.read_text()
            t = t.replace("summary: <!-- kb:todo -->", f"summary: {p.stem} 一句话")
            t = t.replace("- <!-- kb:todo -->", f"- {JAVA_BASE}order/OrderController.java:8 — 查询订单入口")
            p.write_text(t)

    def test_draft_lint_freeze_status_recall(self):
        self.scan()
        self.f("kb", "plan")
        r = self.f("kb", "status", ok=False)
        self.assertEqual(r.returncode, 11)
        self.f("kb", "draft", "--all")
        kb = self.repo / ".ai" / "kb"
        order = kb / "modules" / "order.md"
        self.assertTrue(order.exists())
        self.assertFalse((kb / "modules" / "user.md").exists())
        self.assertTrue((kb / "modules.md").exists())
        self.assertIn("user", (kb / "modules.md").read_text())
        head = order.read_text().split("---")[1]
        self.assertIn("module: order", head)
        self.assertIn('"order"', head)
        self.assertIn("t_order", head)
        facts = self.f("kb", "facts", "--page", "module-order").stdout
        self.assertIn("GET /api/orders/{id} → " + JAVA_BASE + "order/OrderController.java:8 (getOrder)", facts)
        self.assertIn("被 user 依赖", facts)
        r = self.f("kb", "lint", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("summary 未填写", r.stdout)
        self.fill_pages()
        self.f("kb", "lint")
        # 坏引用
        t = order.read_text()
        order.write_text(t + "\n看 foo/bar.java:12 和 " + JAVA_BASE + "order/OrderEntity.java:9999 。\n")
        r = self.f("kb", "lint", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("foo/bar.java:12", r.stdout)
        self.assertIn("超出文件行数", r.stdout)
        order.write_text(t + "\n## 人工补充\n\n<!-- kb:manual -->\n手写的说明\n<!-- /kb:manual -->\n")
        self.f("kb", "freeze")
        idx = (kb / "index.md").read_text()
        self.assertIn("[module-order](modules/order.md)", idx)
        self.assertIn("| order 一句话 |", idx)
        self.assertIn("modules.md", idx)
        man = json.loads((kb / "_manifest.json").read_text())
        self.assertIn(JAVA_BASE + "order/OrderController.java", man["pages"]["module-order"]["sources"])
        r = self.f("kb", "status", ok=False)
        self.assertEqual(r.returncode, 0, r.stdout)
        # flowctl status 也带知识库行
        self.f("new", "kb-check")
        self.assertIn("知识库      新鲜", self.f("status").stdout)
        # 改源码 → 过期
        self.write(JAVA_BASE + "order/OrderController.java", (self.repo / JAVA_BASE / "order/OrderController.java").read_text() + "// x\n")
        r = self.f("kb", "status", ok=False)
        self.assertEqual(r.returncode, 10)
        self.assertRegex(r.stdout, r"module-order\s+过期")
        self.commit("touch")
        self.assertEqual(self.f("kb", "status", ok=False).returncode, 10)
        # 手改与保护：protected 页不被 --force 覆盖；非 protected 页保留 manual 块
        arch = kb / "architecture.md"
        arch.write_text(arch.read_text().replace("protected: false", "protected: true"))
        before = arch.read_text()
        self.f("kb", "scan")
        r = self.f("kb", "draft", "--force", "--page", "architecture")
        self.assertIn("protected", r.stdout)
        self.assertEqual(arch.read_text(), before)
        self.f("kb", "draft", "--force", "--page", "module-order")
        t = order.read_text()
        self.assertIn("手写的说明", t)
        self.assertIn("summary: order 一句话", t)
        self.assertIn("<!-- kb:todo -->", t.split("## 引用文件")[1])
        # recall
        r = self.f("kb", "recall", "--text", "订单导出：给 order 加一个导出接口", "--json")
        res = json.loads(r.stdout)
        self.assertEqual(res[0]["id"], "module-order")
        r = self.f("kb", "recall", "--paths", JAVA_BASE + "user/UserService.java", "--json")
        self.assertEqual(json.loads(r.stdout)[0]["id"], "modules")  # user 并在 modules.md 里，按目录 glob 命中

    def test_agent_scout_gets_kb(self):
        self.scan()
        self.f("kb", "plan")
        self.f("kb", "draft", "--all")
        self.fill_pages()
        self.f("kb", "freeze")
        self.f("new", "exp", "--title", "订单导出")
        d = self.change_dir()
        (d / "proposal.md").write_text("导出 order 列表\n")
        log = self.t / "prompt.log"
        self.f("agent", "scout", "--host", "claude", "--engine", "codex", "--input", str(d / "proposal.md"),
               FAKE_PROMPT_LOG=str(log))
        self.assertIn("知识库召回", log.read_text())
        self.assertIn("module-order", log.read_text())


class GoRepo(Base):
    def setUp(self):
        super().setUp()
        self.write("go.mod", "module example.com/shop\n\ngo 1.22\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n\tgorm.io/gorm v1.25.0 // indirect\n)\n")
        self.write("cmd/api/main.go", """package main

import (
	"github.com/gin-gonic/gin"
	"example.com/shop/internal/order"
)

func main() {
	r := gin.Default()
	r.GET("/orders/:id", order.Get)
	r.POST("/orders", order.Create)
	r.Run()
}
""")
        self.write("internal/order/model.go", """package order

type Order struct {
	ID   int64  `gorm:"column:id"`
	Name string `gorm:"column:name"`
}

func (Order) TableName() string { return "orders" }
""")
        self.write("internal/order/service.go", "package order\n\nimport \"net/http\"\n\nfunc Get(r *http.Request) { http.Get(\"http://x\"); _ = r.Header.Get(\"Content-Type\"); bus.Subscribe(\"x\") }\nfunc Create() {}\n")
        self.write("internal/order/service_test.go", "package order\n\nimport \"testing\"\n\nfunc TestGet(t *testing.T) {}\n")
        self.write(".golangci.yml", "run: {}\n")
        self.commit("go")
        self.f("init")

    def test_scan(self):
        self.f("kb", "scan")
        s = json.loads((self.repo / ".ai" / "kb" / "scan.json").read_text())
        self.assertEqual(s["build"]["ecosystems"], ["go"])
        self.assertEqual(s["build"]["manifests"][0]["name"], "example.com/shop")
        deps = {d["artifact"]: d for d in s["build"]["manifests"][0]["dependencies"]}
        self.assertEqual(deps["github.com/gin-gonic/gin"]["version"], "v1.9.1")
        self.assertTrue(deps["gorm.io/gorm"]["indirect"])
        self.assertEqual(s["build"]["commands"]["test"]["cmd"], "go test ./...")
        self.assertEqual(s["build"]["commands"]["lint"]["cmd"], "golangci-lint run")
        ents = {e["name"]: e for e in s["entries"]}
        self.assertEqual(ents["GET /orders/:id"]["detail"]["handler"], "order.Get")
        self.assertEqual(ents["GET /orders/:id"]["detail"]["framework"], "gin")
        self.assertEqual(ents["main"]["module"], "cmd/api")
        self.assertEqual(sorted(e["kind"] for e in s["entries"]), ["http", "http", "main"])  # Header.Get / Subscribe 不算入口
        m = s["models"][0]
        self.assertEqual((m["name"], m["detail"]["table"], m["module"]), ("Order", "orders", "internal/order"))
        self.assertEqual(s["edges"], [{"from": "cmd/api", "to": "internal/order", "count": 1}])
        self.assertEqual(s["tests"]["by_module"], {"internal/order": 1})
        self.assertEqual([x["name"] for x in s["externals"]], ["http.Get"])


class PyRepo(Base):
    def setUp(self):
        super().setUp()
        self.write("pyproject.toml", """[project]
name = "shop"
version = "0.1.0"
dependencies = [
  "fastapi>=0.100",
  "sqlalchemy",
  "celery",
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 120
""")
        self.write("src/shop/__init__.py", "")
        self.write("src/shop/api/__init__.py", "")
        self.write("src/shop/api/routes.py", """from fastapi import APIRouter
from shop.models import Order
from ..tasks import notify

router = APIRouter()


@router.post("/orders")
async def create_order():
    return Order()
""")
        self.write("src/shop/models.py", """from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Order(Base):
    __tablename__ = "orders"
    id = 1
""")
        self.write("src/shop/tasks.py", """from celery import shared_task
import requests


@shared_task
def notify():
    requests.post("http://x")
""")
        self.write("tests/test_routes.py", "import pytest\n\ndef test_x():\n    assert True\n")
        self.commit("py")
        self.f("init")

    def test_scan(self):
        self.f("kb", "scan")
        s = json.loads((self.repo / ".ai" / "kb" / "scan.json").read_text())
        self.assertEqual(s["build"]["ecosystems"], ["python"])
        man = s["build"]["manifests"][0]
        self.assertEqual(man["name"], "shop")
        self.assertEqual([d["artifact"] for d in man["dependencies"]], ["fastapi", "sqlalchemy", "celery"])
        self.assertEqual(s["build"]["commands"]["test"]["cmd"], "python -m pytest -q")
        self.assertEqual(s["build"]["commands"]["lint"]["cmd"], "ruff check .")
        ents = {e["name"]: e for e in s["entries"]}
        self.assertEqual(ents["POST /orders"]["detail"]["handler"], "create_order")
        self.assertEqual(ents["notify"]["kind"], "mq")
        m = s["models"][0]
        self.assertEqual((m["name"], m["detail"]["table"]), ("Order", "orders"))
        self.assertEqual({x["id"] for x in s["modules"]}, {"shop", "shop.api"})
        self.assertIn({"from": "shop.api", "to": "shop", "count": 2}, s["edges"])
        self.assertEqual([x["name"] for x in s["externals"]], ["requests.post"])
        self.assertEqual(s["tests"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
