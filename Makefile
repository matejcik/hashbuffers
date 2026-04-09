.PHONY: style

style:
	uv run black src/ tests/
	uv run isort src/ tests/
	uv run pyright

test:
	uv run pytest
