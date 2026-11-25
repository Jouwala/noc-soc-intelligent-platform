# -*- coding: utf-8 -*-
"""FW 日报「邮件失败错误」保留策略回归（2026-11-25 事故）。

事故经过：11-25 07:15/07:20 两次 FW 日报发信失败，真实异常是 .NET/EWS 的
``Unable to connect to the remote server``。``fw_kpi_services.run()`` 当时用
``str(reason)[:800]`` 只保留头部，而异常行在子进程输出的尾部 —— 被砍掉后：

1. 任务历史里只剩半句 ``Exception calling "Ge``，UI 上无法排障；
2. 更严重的是 ``email_recovery.is_ews_resendable()`` 拿同一个 error 串做判据，
   匹配不到 ``EWS_FAIL_KEYS`` → 判 False → FW 日报在通道恢复后**从未进入补发链**。

所以这里的断言不是「字面量好看」，而是锁住一条功能契约：保存下来的 error
必须同时保留头部上下文与尾部真实异常，且尾部关键字能被补发判据识别。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.email import email_recovery as er  # noqa: E402
from backend.services.fw_kpi import fw_kpi_services as fw  # noqa: E402


def _dotnet_output() -> str:
    """构造与 11-25 现场同形的长输出：头部是发送上下文，尾部是 .NET 真实异常。"""
    lines = [
        "[RECIPIENTS] 主送 2 人（来源: --to），抄送 1 人（来源: --cc）",
        "[DISCOVER] 内嵌图片(2): FW_Whole_Throughput_37781505.png, "
        "FW_Whole_Throughput_37781506.png",
        "[SEND] 通过 .NET HttpWebRequest + NTLM 发送到 exchmail.example.com...",
        "       To : ['noc@example.com', 'ops@example.com']",
        "       Cc : ['lead@example.com']",
        "       Sub: FW Whole Throughput Report - 2026-11-25",
        "       Img: ['FW_Whole_Throughput_37781505.png', "
        "'FW_Whole_Throughput_37781506.png'] (inline)",
    ]
    # 中间部分：把总长度推过 4000，确认「只留头」会丢掉尾部异常。
    lines += [f"[TRACE] 第 {i:03d} 步：NTLM 握手 / 附件上传重试中……" for i in range(1, 121)]
    lines += [
        "[SEND][FAIL] Exception calling \"GetRequestStream\" with \"0\" "
        "argument(s): \"Unable to connect to the remote server\"",
    ]
    return "\n".join(lines)


def test_summary_keeps_tail_exception_and_is_resendable():
    raw = _dotnet_output()
    assert len(raw) > fw._ERROR_SUMMARY_LIMIT  # 前置条件：确实超限

    summary = fw._summarize_email_error(raw)

    # 1) 有界：不能把整段输出原样塞进任务记录
    assert len(summary) <= fw._ERROR_SUMMARY_LIMIT + 80
    # 2) 头部上下文还在（排障要知道发给谁、发到哪）
    assert "[RECIPIENTS]" in summary
    assert "[SEND] 通过 .NET HttpWebRequest" in summary
    # 3) 尾部真实异常必须还在
    assert "Unable to connect to the remote server" in summary
    assert summary.rstrip().endswith("Unable to connect to the remote server\"")
    # 4) 明确标出省略，避免读者以为错误串只有这么长
    assert "中间省略" in summary
    # 5) 功能契约：补发判据能识别（这正是事故里失效的那一环）
    assert er.is_ews_resendable(summary) is True


def test_head_only_truncation_would_break_resend_chain():
    """反证：还原旧写法 ``raw[:800]``，补发判据返回 False —— 事故复现。"""
    raw = _dotnet_output()
    legacy = raw[:800]
    assert "Unable to connect to the remote server" not in legacy
    assert er.is_ews_resendable(legacy) is False


def test_summary_keeps_short_error_verbatim():
    raw = "  [WinError 10065] No route to host  \n"
    assert fw._summarize_email_error(raw) == "[WinError 10065] No route to host"
    # 短错误原样保留，且非 EWS 失败仍不会被误判为可补发
    assert er.is_ews_resendable(fw._summarize_email_error(raw)) is False


def test_summary_handles_non_string_reason():
    """reason 可能是 dict（email_result 兜底 str()），不得抛异常。"""
    assert fw._summarize_email_error({"success": False}) == "{'success': False}"
    assert fw._summarize_email_error(None) == ""
