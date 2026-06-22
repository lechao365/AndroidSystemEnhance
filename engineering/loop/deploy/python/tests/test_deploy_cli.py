"""deploy cli 单元测试（decide dry-run）。"""
import argparse
from loop_deploy.cli import add_deploy_parser


class _Args:
    pass


def test_dry_run_decide():
    parser = argparse.ArgumentParser()
    add_deploy_parser(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["deploy", "--decide", "--diff-rev", "HEAD"])
    assert args.decide is True
    assert args.diff_rev == "HEAD"
