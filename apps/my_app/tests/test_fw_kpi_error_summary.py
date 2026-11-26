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
    """未超限必须**原样**返回：不 strip，否则判据输入被改写（2026-11-25 复审）。"""
    raw = "  [WinError 10065] No route to host  \n"
    assert fw._summarize_email_error(raw) == raw
    # 短错误原样保留，且非 EWS 失败仍不会被误判为可补发
    assert er.is_ews_resendable(fw._summarize_email_error(raw)) is False


def test_summary_handles_non_string_reason():
    """reason 可能是 dict（email_result 兜底 str()），不得抛异常。"""
    assert fw._summarize_email_error({"success": False}) == "{'success': False}"
    assert fw._summarize_email_error(None) == ""


def test_summary_does_not_swallow_falsy_values():
    """falsy 值（0 / False / 空容器）必须各自 str()，不能一律变空串。"""
    assert fw._summarize_email_error(0) == "0"
    assert fw._summarize_email_error(False) == "False"
    assert fw._summarize_email_error("") == ""
    assert fw._summarize_email_error([]) == "[]"


# ---------------------------------------------------------------------------
# 脱敏（2026-11-25 复审）：账号 / 主机 / 凭据不得随错误摘要落库或进日志
# ---------------------------------------------------------------------------

def test_summary_redacts_recipients_and_mail_host():
    """摘要进 task_execution_log 与运行日志前，必须抹掉收件账号与内部主机。"""
    summary = fw._summarize_email_error(_dotnet_output())

    for leaked in ("noc@example.com", "ops@example.com", "lead@example.com",
                   "exchmail.example.com"):
        assert leaked not in summary, f"未脱敏: {leaked}"

    # 排障上下文与补发判据关键字一个都不能少
    assert "[RECIPIENTS]" in summary
    assert "[SEND] 通过 .NET HttpWebRequest" in summary
    assert "Unable to connect to the remote server" in summary
    assert er.is_ews_resendable(summary) is True


def test_redact_keeps_ews_keywords_resendable():
    """脱敏只换身份信息，不能顺手把 EWS_FAIL_KEYS 的措辞换掉。"""
    cases = (
        "Unable to connect to the remote server",
        "The remote name could not be resolved: 'exchmail.corp.local'",
        "[WinError 10054] 连接被重置",
        "CreateItem failed: SOAP fault",
        "SendItem 退避中 (RateLimit)",
    )
    for raw in cases:
        assert er.is_ews_resendable(fw._redact_email_error(raw)) is True, raw
    # 非 EWS 失败不得因为脱敏反而"变成"可补发
    assert er.is_ews_resendable(fw._redact_email_error("本地磁盘已满")) is False


def test_redact_hides_url_credentials_and_ip():
    raw = ("发送失败: https://svc:Passw0rd@exchmail.example.com/EWS/Exchange.asmx "
           "via 10.100.2.101")
    out = fw._redact_email_error(raw)

    assert "Passw0rd" not in out
    assert "svc:" not in out
    assert "exchmail.example.com" not in out
    assert "10.100.2.101" not in out
    assert "发送失败" in out


def test_redact_leaves_plain_text_untouched():
    """没有身份信息的错误串保持原样，别为了脱敏改了排障信息。"""
    assert fw._redact_email_error("") == ""
    plain = "普通错误: 连接超时，Exchange.asmx 返回 503"
    assert fw._redact_email_error(plain) == plain


def test_redact_email_result_in_place():
    """`result["email"]["error"]` 与顶层 error 同样会落库，必须一起脱敏。"""
    res = {"success": False,
           "error": "To : ['noc@example.com'] 主机 exchmail.example.com 失败"}
    fw._redact_email_result(res)
    assert "noc@example.com" not in res["error"]
    assert "exchmail.example.com" not in res["error"]

    # 无 error / 非 dict 的输入不得抛异常
    assert fw._redact_email_result({"success": True}) == {"success": True}
    assert fw._redact_email_result(None) is None
