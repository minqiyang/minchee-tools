# Adding a tool

1. Pick the category from the layout in the top-level README. If none fits, add a category folder with its own
   README that says what belongs there and how its tools are installed.
2. Create one folder for the tool inside that category, named in lowercase with hyphens.
3. Put these in the folder:
   - `README.md` with the sections What it does, Why, Install, Configure, Test, and Limits (skip the ones that do not
     apply);
   - the code, if any, with `test_*.py` tests next to it (standard library only where possible);
   - an `install.sh` when installing takes more than one copy step. It backs up anything it changes and is safe to
     run twice.
4. Add a line for the tool to its category README and to the Tools table in the top-level README.
5. Run `scripts/run-tests.sh`.

Rules for everything in this repository:

- English only.
- No secrets, tokens, account identifiers, email addresses, or personal paths. Read them from the environment or
  from files outside the repository at run time.
- State limits plainly. A tool that checks only part of what its name suggests says so in its README and its output.
