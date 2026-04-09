.PHONY: style

style:
	uv run black src/ tests/
	uv run isort src/ tests/
	uv run pyright

test:
	uv run pytest

coverage:
	uv run pytest --cov-report=html
	@echo "HTML report: htmlcov/index.html"
