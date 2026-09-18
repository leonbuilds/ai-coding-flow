.PHONY: test doctor sync sync-dry uninstall demo demo-codex

test:          ## 全部测试（临时目录，不碰真实配置）
	python3 -m unittest discover -s tests -v

doctor:        ## 体检两端环境
	python3 workbench/wb.py doctor

sync-dry:      ## 预览 sync 会做什么
	python3 workbench/wb.py sync --dry-run

sync:          ## 安装 / 更新到 Claude Code 与 Codex
	python3 workbench/wb.py sync

uninstall:
	python3 workbench/wb.py uninstall

demo:          ## 用测试替身跑一遍完整产线，重新生成 examples/export-orders
	bash examples/demo.sh

demo-codex:    ## 同上，但审查用真实 Codex（会消耗 token）
	REVIEW_ENGINE=codex REVIEW_MODEL=$(MODEL) REVIEW_REASONING=$(REASONING) bash examples/demo.sh
