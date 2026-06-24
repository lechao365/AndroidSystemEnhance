from pathlib import Path

from loop_core.cli import main


def test_gen_cases_validate_good_yaml(tmp_path: Path):
    suite_file = tmp_path / "good_suite.yaml"
    suite_file.write_text("""\
suite: test
version: "1.0"
cases:
  - id: shell.reachable
    command: echo hello
    run_on: host
    assert:
      type: contains
      value: hello
""", encoding="utf-8")
    rc = main(["gen-cases", "--validate", str(suite_file)])
    assert rc == 0


def test_gen_cases_validate_bad_assert_type(tmp_path: Path):
    suite_file = tmp_path / "bad_assert.yaml"
    suite_file.write_text("""\
suite: test
version: "1.0"
cases:
  - id: bad.assert
    command: echo hi
    run_on: host
    assert:
      type: invalid_type
      value: x
""", encoding="utf-8")
    rc = main(["gen-cases", "--validate", str(suite_file)])
    assert rc == 1


def test_gen_cases_validate_duplicate_id(tmp_path: Path):
    suite_file = tmp_path / "dup_id.yaml"
    suite_file.write_text("""\
suite: test
version: "1.0"
cases:
  - id: dup.case
    command: echo a
    run_on: host
    assert:
      type: contains
      value: a
  - id: dup.case
    command: echo b
    run_on: host
    assert:
      type: contains
      value: b
""", encoding="utf-8")
    rc = main(["gen-cases", "--validate", str(suite_file)])
    assert rc == 1


def test_gen_cases_validate_lciod_suite():
    cases_dir = "engineering/loop/cases/features/lciod"
    files = [str(p) for p in Path(cases_dir).glob("*.yaml")]
    assert files, "no lciod suite yaml found"
    rc = main(["gen-cases", "--validate"] + files)
    assert rc == 0


def test_gen_cases_validate_lcview_suite():
    cases_dir = "engineering/loop/cases/features/lcview"
    files = [str(p) for p in Path(cases_dir).glob("*.yaml")]
    assert files, "no lcview suite yaml found"
    rc = main(["gen-cases", "--validate"] + files)
    assert rc == 0


def test_gen_cases_validate_yaml_syntax_error(tmp_path: Path):
    suite_file = tmp_path / "bad_syntax.yaml"
    suite_file.write_text("suite: test\n  bad: : :\n", encoding="utf-8")
    rc = main(["gen-cases", "--validate", str(suite_file)])
    assert rc == 1


def test_gen_cases_validate_missing_suite_key(tmp_path: Path):
    suite_file = tmp_path / "no_suite.yaml"
    suite_file.write_text('version: "1.0"\ncases: []\n', encoding="utf-8")
    rc = main(["gen-cases", "--validate", str(suite_file)])
    assert rc == 1
