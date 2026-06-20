from pathlib import Path


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    while path.name != "engineering":
        if path == path.parent:
            raise RuntimeError("engineering/ root not found")
        path = path.parent
    return path.parent


def _read(relative_path: str) -> str:
    return (_repo_root() / relative_path).read_text(encoding="utf-8")


def test_diagnosis_template_uses_fact_based_sections() -> None:
    text = _read("engineering/harness/templates/diagnosis-report-template.md")

    assert "## 3. 现象归类与不确定性" in text
    assert "## 4. 调查线索（用户提供，未验证）" in text
    assert "## 5. 候选修复方向（人工执行）" in text
    assert "不强行下唯一根因结论" in text
    assert "根因假设" not in text
    assert "## 3. 根因分析" not in text


def test_workflow_documents_fail_path_and_optional_user_clues() -> None:
    text = _read("engineering/loop/WORKFLOW.md")

    assert "有 fail → AI 读 EvidenceBundle 分析证据并收敛候选修复方向" in text
    assert "AI 生成候选补丁草案（人工确认后再实施）" in text
    assert "任何 FAIL 都进入诊断阶段" in text
    assert "调查线索（用户提供，未验证）" in text
    assert "不强行给唯一根因" in text


def test_le_command_describes_diagnosis_and_patch_draft_flow() -> None:
    text = _read(".opencode/commands/le.md")

    assert "诊断报告" in text
    assert "候选补丁草案" in text
    assert "人工确认" in text
    assert "用户线索" in text
