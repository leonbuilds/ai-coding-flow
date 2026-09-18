#!/usr/bin/env bash
# 在临时仓库里把一个需求完整走一遍产线，产出 examples/export-orders/ 这份样例档案。
#
#   examples/demo.sh                              审查用测试替身（不花 token）
#   REVIEW_ENGINE=codex REVIEW_MODEL=gpt-5.6-luna REVIEW_REASONING=low examples/demo.sh
#   REVIEW_ENGINE=claude REVIEW_MODEL=sonnet examples/demo.sh
#
# 实现部分由脚本模拟（写入预置的首版和终版代码），审查部分可以接真实模型。
set -euo pipefail
KIT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$KIT/examples/src"
ENGINE="${REVIEW_ENGINE:-fake}"
# 测试替身的结果另存，避免覆盖用真实模型审查过的样例档案
if [ "$ENGINE" = "fake" ]; then OUT="${OUT:-$KIT/examples/.demo-fake}"; else OUT="${OUT:-$KIT/examples/export-orders}"; fi
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

flowctl() { python3 "$KIT/skills/flow/scripts/flowctl.py" "$@"; }
if [ "$ENGINE" = "fake" ]; then export PATH="$KIT/tests/fakes:$PATH"; fi
export HOME="$WORK/home"; mkdir -p "$HOME"   # 隔离 ~/.ai-flow，不影响真实配置
if [ "$ENGINE" != "fake" ]; then  # 真实引擎要用自己的登录态：把真实配置目录链进临时 HOME
  REAL_HOME="$(eval echo ~"$(id -un)")"
  ln -s "$REAL_HOME/.codex" "$HOME/.codex" 2>/dev/null || true
  ln -s "$REAL_HOME/.claude" "$HOME/.claude" 2>/dev/null || true
  ln -s "$REAL_HOME/.claude.json" "$HOME/.claude.json" 2>/dev/null || true
fi

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fill() {  # 用预置的已填写文档替换模板，并做占位符替换
  sed -e "s/{{change}}/$CHANGE/g" -e "s/{{date}}/$(date +%F)/g" "$SRC/change/$1" > "$DIR/$1"
}

step "准备一个小型订单服务仓库"
cd "$WORK" && mkdir repo && cd repo
git init -q && git config user.name demo && git config user.email demo@example.com
cp -R "$SRC/toy/." .
git add -A && git commit -qm "init: order service"
python3 -m unittest discover -s tests -q

step "接入：flowctl init + 一条领域 spec + starter 里的 review 检查项"
flowctl init >/dev/null
cp "$SRC/specs/domain-money-cents.md" .ai/specs/domain/
cp "$KIT/specs-starter/review/"*.md .ai/specs/review/
flowctl specs lint

step "① propose"
flowctl new export-orders --title "订单批量导出 CSV"
CHANGE="$(cat .ai/current)"; DIR=".ai/changes/$CHANGE"
flowctl recall --text "运营需要按订单状态把订单批量导出成 CSV，字段含金额（元）" --paths orders/service.py --record
fill proposal.md
flowctl trace proposal generated; flowctl trace proposal accepted

step "② design / ③ tasks"
fill design.md; flowctl trace design generated; flowctl trace design generated; flowctl trace design accepted
fill tasks.md;  flowctl trace tasks generated;  flowctl trace tasks accepted

step "④ build · T1：首版漏了分转元，验收失败后修正"
flowctl snapshot T1 base
cat > orders/export.py <<'EOF'
import csv
import io
from typing import List

from orders.model import Order


def to_csv(orders: List[Order]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "customer", "status", "amount_yuan"])
    for o in orders:
        w.writerow([o.id, o.customer, o.status, o.amount_cents])
    return buf.getvalue()
EOF
cat > tests/test_export.py <<'EOF'
import unittest

from orders.export import to_csv
from orders.model import Order


class TestExport(unittest.TestCase):
    def test_header_only_when_empty(self):
        self.assertEqual(to_csv([]), "id,customer,status,amount_yuan\n")

    def test_amount_in_yuan(self):
        out = to_csv([Order(7, "张三", "paid", 12345)])
        self.assertEqual(out.splitlines()[1], "7,张三,paid,123.45")

    def test_comma_in_customer_is_quoted(self):
        out = to_csv([Order(8, "a,b", "paid", 100)])
        self.assertEqual(out.splitlines()[1], '8,"a,b",paid,1.00')


if __name__ == "__main__":
    unittest.main()
EOF
flowctl snapshot T1 v1
python3 -m unittest tests.test_export -q 2>/dev/null && echo "（不应该通过）" || echo "验收失败：金额没有转换成元 → 修正"
sed -i.bak 's/o.amount_cents\]/f"{o.amount_cents \/ 100:.2f}"]/' orders/export.py && rm orders/export.py.bak
python3 -m unittest tests.test_export -q
flowctl snapshot T1 final
git add -A . ':!.ai' && git commit -qm "T1: to_csv"

step "④ build · T2：一次写对"
flowctl snapshot T2 base
cat > orders/service.py <<'EOF'
from orders.export import to_csv
from orders.repo import InMemoryOrderRepo


class OrderService:
    def __init__(self, repo: InMemoryOrderRepo):
        self.repo = repo

    def count_by_status(self, status: str) -> int:
        return len(self.repo.list_by_status(status))

    def export_csv(self, status: str) -> str:
        return to_csv(self.repo.list_by_status(status))
EOF
python3 - <<'EOF'
p = "tests/test_service.py"
s = open(p).read().replace('''

if __name__''', '''
    def test_export_csv_filters_by_status(self):
        svc = OrderService(InMemoryOrderRepo([Order(1, "a", "paid", 100), Order(2, "b", "created", 50)]))
        self.assertEqual(svc.export_csv("paid"), "id,customer,status,amount_yuan\\n1,a,paid,1.00\\n")


if __name__''')
open(p, "w").write(s)
EOF
flowctl snapshot T2 v1
python3 -m unittest discover -s tests -q
flowctl snapshot T2 final
git add -A . ':!.ai' && git commit -qm "T2: OrderService.export_csv"

step "⑤ review · 引擎：$ENGINE"
case "$ENGINE" in
  fake)   flowctl verify start --host claude --host-model opus --engine codex --model fake-model ;;
  codex)  flowctl verify start --host claude --host-model opus --engine codex --model "${REVIEW_MODEL:-}" \
            ${REVIEW_REASONING:+--reasoning "$REVIEW_REASONING"} --timeout 1200 ;;
  claude) flowctl verify start --host codex --engine claude --model "${REVIEW_MODEL:-sonnet}" --timeout 1200 ;;
esac || { echo "审查未通过或本轮无效，档案仍会导出以便查看"; }
flowctl score domain-money-cents hit --note "T1 修正依据"
python3 - "$DIR/review.md" <<'EOF'
import sys
p = sys.argv[1]
s = open(p).read().replace("<!-- flow:todo -->\n", "")
s += "| domain-money-cents | hit | T1 首版违反，验收失败后按它修正 |\n"
open(p, "w").write(s)
EOF

step "⑥ retro"
mkdir -p .ai/specs/review
cat > .ai/specs/review/checked-domain-specs.md <<'EOF'
---
id: review-checked-domain-specs
type: review
when: 每次审查都检查
always: true
status: active
---

# proposal 里勾选过的 domain spec，逐条确认实现是否遵守

读 proposal.md 上下文清单中勾选的 domain 类 spec，对照 diff 逐条判断「遵守 / 违反（位置）」。spec 被召回了不代表被执行了。
EOF
flowctl specs lint
flowctl metrics
fill retro.md
flowctl close
flowctl report

step "导出样例档案 → $OUT"
rm -rf "$OUT" && mkdir -p "$OUT"
cp -R "$DIR/." "$OUT/"
mkdir -p "$OUT/_repo-after" && git archive HEAD | tar -x -C "$OUT/_repo-after"
cp -R .ai/specs "$OUT/_specs"
# 档案里的绝对路径换成占位，避免把本机临时目录带进样例
grep -rlF "$WORK" "$OUT" 2>/dev/null | while read -r f; do
  python3 -c 'import sys;p,w=sys.argv[1:];s=open(p,encoding="utf-8").read();open(p,"w",encoding="utf-8").write(s.replace(w,"/tmp/demo").replace("/private"+w,"/tmp/demo"))' "$f" "$WORK"
done
echo "完成：$(find "$OUT" -type f | wc -l | tr -d ' ') 个文件"
