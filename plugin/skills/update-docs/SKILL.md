# Update Documentation

Update documentation files to reflect code changes. This skill emphasizes
**verification**, not just generation.

## When to use

- After making code changes that affect user-facing behavior
- When adding new CLI flags, configuration options, or API endpoints
- When changing defaults, removing features, or modifying error messages

## Steps

1. **Identify affected docs**: Use the `find_docs_for_code` MCP tool with the
   paths of files you changed. If the tool is not available, look for doc files
   that reference the changed modules.

2. **Read the current doc**: Read the full content of each affected doc file.

3. **Check style guidelines**: If `.code-to-docs/style.md` exists, read it and
   follow those conventions.

4. **Check ignore list**: If `.code-to-docs/ignore` exists, skip any files that
   match the patterns.

5. **Make the update**: Edit the doc file to reflect the code change. Be
   comprehensive but stay grounded:
   - Document new parameters, options, flags with names, types, defaults
   - Update existing examples that are now outdated
   - Do NOT remove content unrelated to your change
   - Do NOT reorganize sections that are unaffected

6. **Verify the update**:
   - Run the CLI tool (if applicable) and compare `--help` output against the docs
   - Check that code samples in the docs are syntactically valid
   - If a docs build command is available, run it
   - Diff the documented behavior against actual behavior for at least one claim

7. **Check the diff**: Review your changes. Every line you added should trace
   back to something in the code change. Every line you removed should be
   something the code change made incorrect.
