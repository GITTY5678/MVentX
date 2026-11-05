# Contributing to MVentX

Thank you for your interest in contributing to **MVentX (Multi-Environment Boot Experience)**!

MVentX is a community-driven multiboot USB project. We welcome contributions in code, documentation, testing, boot method recipes, and UI improvements.

---

## Code of Conduct

All contributors are expected to uphold a welcoming, respectful, and harassment-free environment for everyone.

---

## Architectural Guidelines

Before submitting PRs, ensure your contribution respects the core architectural invariants:

1. **Bootloader Independence**: GRUB configurations and EFI boot stages must remain completely decoupled from Python or PySide6 runtimes.
2. **No Hardcoded Operating Systems**: No distro (Ubuntu, Kali, Windows, Arch, etc.) should be treated as mandatory or hardcoded into the default menus.
3. **Explicit User Registration**: Do not submit automated ISO-scanning algorithms that bypass explicit user registration.
4. **Safety & Non-Destructive Operations**: Never introduce automatic partitioning, disk formatting, or unprompted file overwrites. Destructive actions must always be isolated in dedicated installer modules with explicit confirmation guards.
5. **Self-Contained Target**: Configuration files, registration databases, and payload state must live on the target USB drive.

---

## Development Workflow

1. **Fork & Branch**:
   - Fork the repository and create your branch from `main`:
     ```bash
     git checkout -b feature/your-feature-name
     ```

2. **Environment Setup**:
   - Install dev dependencies using Python 3.10+:
     ```bash
     python -m venv .venv
     source .venv/bin/activate  # Windows: .venv\Scripts\activate
     pip install -e ".[dev]"
     ```

3. **Coding Standards**:
   - Format Python code with `black` and `ruff`.
   - Ensure type annotations adhere to `mypy` strict guidelines where feasible.
   - Keep functions focused and modular.

4. **Testing**:
   - Write unit tests for all business logic in `tests/unit/`.
   - Write integration tests for configuration generator logic in `tests/integration/`.
   - Run tests before committing:
     ```bash
     pytest
     ```

5. **Commit Messages**:
   - Use conventional commit messages: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.

---

## Submitting Pull Requests

1. Open a Pull Request targeting the `main` branch.
2. Provide a clear summary of changes, problem statement, and testing steps.
3. Ensure CI checks pass.

