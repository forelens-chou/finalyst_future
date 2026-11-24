"""
Notification and reporting bus: Rich console rendering, JSON archiving, and extensible EmailNotifier.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.config import StrategyConfig

logger = logging.getLogger("divergence.notifier")
console = Console()


def _json_serial_default(obj: Any) -> Any:
    """Handle numpy types during JSON serialization."""
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, (datetime, Path)):
        return str(obj)
    return str(obj)


class Notifier:
    """
    Handles terminal dashboard display, local JSON archiving, and outbound dispatching.
    """

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.report_dir = config.resolved_report_dir

    def print_daily_candidates(self, candidates: List[Dict[str, Any]]) -> None:
        """
        Render Daily Screener candidate pool in a Rich Table.
        """
        table = Table(
            title=f"📊 [bold cyan]日线级别关键 S/R 候选池 (Daily Candidate Pool)[/bold cyan] - 共 {len(candidates)} 个标的",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("品种", style="bold yellow", width=8)
        table.add_column("名称", style="cyan", width=10)
        table.add_column("板块", style="dim", width=10)
        table.add_column("现价", justify="right")
        table.add_column("热区类型", justify="center")
        table.add_column("热区范围", justify="center")
        table.add_column("距离", justify="right")
        table.add_column("预期方向", justify="center")
        table.add_column("1D ADX", justify="right")
        table.add_column("优先级", justify="center")

        for c in candidates:
            zone_color = "red" if c["zone_type"] == "RESISTANCE" else "green"
            dir_color = "bold red" if c["expected_direction"] == "SHORT" else "bold green"
            dir_text = "做空 (SHORT)" if c["expected_direction"] == "SHORT" else "做多 (LONG)"
            pri_style = "bold magenta" if c["priority"] == "S_TIER" else "white"

            table.add_row(
                c["symbol"],
                c["name"],
                c["sector"],
                f"{c['current_price']:.1f}",
                f"[{zone_color}]{c['zone_type']}[/{zone_color}]",
                f"{c['zone_bounds'][0]:.1f} ~ {c['zone_bounds'][1]:.1f}",
                f"{c['distance_to_zone']:.1f}",
                f"[{dir_color}]{dir_text}[/{dir_color}]",
                f"{c['adx_1d']:.1f}",
                f"[{pri_style}]{c['priority']}[/{pri_style}]",
            )

        console.print()
        console.print(table)
        console.print()

    def print_hourly_signals(self, signals: List[Dict[str, Any]]) -> None:
        """
        Render Hourly Scanner divergence signals and trade plans in a Rich Table.
        """
        if not signals:
            console.print(Panel("🔍 [yellow]本次小时级扫描完成，未发现满足共振条件的背离信号。[/yellow]", title="Hourly Scanner"))
            return

        table = Table(
            title=f"🚨 [bold green]小时级背离触发信号与交易计划 (Hourly Scanner Signals)[/bold green] - 共 {len(signals)} 个标的",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("品种", style="bold yellow", width=8)
        table.add_column("名称", style="cyan", width=8)
        table.add_column("方向", justify="center", width=8)
        table.add_column("日线热区", justify="center", width=10)
        table.add_column("共振等级", justify="center", width=10)
        table.add_column("触发价(收盘/触价)", justify="right")
        table.add_column("止损价", justify="right", style="bold red")
        table.add_column("单笔风险(元)", justify="right")
        table.add_column("建议手数(10万本金)", justify="center", style="bold green")
        table.add_column("目标位(TP1/TP2)", justify="center")
        table.add_column("盈亏比(R:R)", justify="right")

        for s in signals:
            plan = s["trade_plan"]
            dir_style = "bold red" if s["direction"] == "SHORT" else "bold green"
            dir_text = "做空" if s["direction"] == "SHORT" else "做多"

            daily_ctx = s.get("daily_context", {}) or {}
            zone_color = "red" if daily_ctx.get("zone_type") == "RESISTANCE" else (
                "green" if daily_ctx.get("zone_type") == "SUPPORT" else "dim"
            )
            zone_name = daily_ctx.get("zone_type") or "无热区"
            zone_disp = f"[{zone_color}]{zone_name}[/{zone_color}]"

            stars = "⭐" * s["resonance_level"]
            triggers = f"{plan['entry_close_trigger']:.1f} / {plan['entry_touch_trigger']:.1f}"
            tps = f"{plan['tp1_382']:.1f} / {plan['tp2_667']:.1f}"

            lots_str = f"{plan['suggested_lots']} 手" if plan["valid_lot_size"] else "[dim]弃单(<1手)[/dim]"

            table.add_row(
                s["symbol"],
                s["name"],
                f"[{dir_style}]{dir_text}[/{dir_style}]",
                zone_disp,
                f"{stars} (L{s['resonance_level']})",
                triggers,
                f"{plan['stop_loss']:.1f}",
                f"{plan['risk_per_lot_cny']:.0f}",
                lots_str,
                tps,
                f"1 : {plan['rr_ratio_tp1']:.2f}",
            )

        console.print()
        console.print(table)
        console.print()

    def archive_json_report(self, mode: str, data: List[Dict[str, Any]]) -> Optional[Path]:
        """
        Archive scan results into reports/YYYYMMDD_HHMM_mode.json.
        """
        if not self.config.output.enable_json_archive:
            return None

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{now_str}_{mode}.json"
        target_path = self.report_dir / filename

        payload = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "count": len(data),
            "results": data,
        }

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_serial_default)

        logger.info(f"Report archived to {target_path}")
        return target_path

    def export_csv_report(self, mode: str, data: List[Dict[str, Any]]) -> Optional[Path]:
        """
        Export scan results into reports/YYYYMMDD_HHMMSS_mode.csv and reports/latest_mode.csv.
        Uses utf-8-sig encoding for seamless compatibility with Microsoft Excel / WPS.
        """
        if not getattr(self.config.output, "enable_csv_archive", True) or not data:
            return None

        rows = []
        if "hourly" in mode:
            for item in data:
                plan = item.get("trade_plan", {})
                div_det = item.get("divergence_details", {})
                close_risk = item.get("close_risk", {})
                daily_ctx = item.get("daily_context", {}) or {}
                bounds = daily_ctx.get("zone_bounds", [])
                bounds_str = f"{bounds[0]:.1f} ~ {bounds[1]:.1f}" if len(bounds) == 2 else "未触发/全品种扫描"
                zone_type_str = "阻力区 (RESISTANCE)" if daily_ctx.get("zone_type") == "RESISTANCE" else (
                    "支撑区 (SUPPORT)" if daily_ctx.get("zone_type") == "SUPPORT" else "无特定热区"
                )
                level = item.get("resonance_level", 1)
                rows.append({
                    "K线时间": item.get("timestamp"),
                    "合约代码": item.get("symbol"),
                    "品种名称": item.get("name"),
                    "所属板块": item.get("sector"),
                    "交易方向": "做多 (LONG)" if item.get("direction") == "LONG" else "做空 (SHORT)",
                    # 方案 1：融合日线大级别宏观上下文
                    "日线优先级": daily_ctx.get("priority", "A_TIER"),
                    "日线S/R热区类型": zone_type_str,
                    "日线热区范围": bounds_str,
                    "距日线热区距离": daily_ctx.get("distance_to_zone", 0.0),
                    "日线ADX趋势值": daily_ctx.get("adx_1d", 0.0),
                    "日线至TP1空间": daily_ctx.get("space_to_tp1", 0.0),
                    # 小时线战术级指标与风控点位
                    "共振星级": f"L{level} (" + "⭐" * level + ")",
                    "综合评分": item.get("score"),
                    "现价": plan.get("current_price"),
                    "收盘确认触发价": plan.get("entry_close_trigger"),
                    "盘中触价触发价": plan.get("entry_touch_trigger"),
                    "止损价": plan.get("stop_loss"),
                    "止损跳数(ticks)": plan.get("risk_ticks"),
                    "单手风险金额(元)": plan.get("risk_per_lot_cny"),
                    "建议手数(10万本金)": plan.get("suggested_lots"),
                    "是否有效下单": "有效" if plan.get("valid_lot_size") else "弃单(<1手)",
                    "第一目标位(TP1_38.2%)": plan.get("tp1_382"),
                    "TP1平仓比例": f"{int(plan.get('tp1_close_percent', 0.6) * 100)}%",
                    "第二目标位(TP2_66.7%)": plan.get("tp2_667"),
                    "预期盈亏比(R:R)": f"1:{plan.get('rr_ratio_tp1', 0.0):.2f}",
                    "MACD双背离": "是" if div_det.get("macd_double_divergence") else "否",
                    "MACD跨零轴": "是" if div_det.get("macd_cross_zero") else "否",
                    "RSI超买超卖背离": "是" if div_det.get("rsi_divergence") else "否",
                    "成交量均量萎缩背离": "是" if div_det.get("volume_divergence") else "否",
                    "持仓量(OI)减仓推高背离": "是" if div_det.get("oi_divergence") else "否",
                    "临近收盘风险": "是" if close_risk.get("risk") else "否",
                })
        elif "daily" in mode:
            for item in data:
                bounds = item.get("zone_bounds", [0, 0])
                rows.append({
                    "日期": item.get("last_date"),
                    "合约代码": item.get("symbol"),
                    "品种名称": item.get("name"),
                    "所属板块": item.get("sector"),
                    "现价": item.get("current_price"),
                    "S/R热区类型": "阻力区 (RESISTANCE)" if item.get("zone_type") == "RESISTANCE" else "支撑区 (SUPPORT)",
                    "热区中心价": item.get("zone_center"),
                    "热区下轨": bounds[0] if len(bounds) > 0 else 0,
                    "热区上轨": bounds[1] if len(bounds) > 1 else 0,
                    "距热区距离": item.get("distance_to_zone"),
                    "预期反转方向": "做空 (SHORT)" if item.get("expected_direction") == "SHORT" else "做多 (LONG)",
                    "日线ATR": item.get("daily_atr"),
                    "预估1H_ATR": item.get("est_1h_atr"),
                    "目标位TP1(38.2%)": item.get("tp1_382"),
                    "目标位TP2(66.7%)": item.get("tp2_667"),
                    "至TP1空间": item.get("space_to_tp1"),
                    "日线ADX": item.get("adx_1d"),
                    "优先级": item.get("priority"),
                })
        else:
            rows = data

        df = pd.DataFrame(rows)
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = self.report_dir / f"{now_str}_{mode}.csv"
        latest_path = self.report_dir / f"latest_{mode}.csv"

        df.to_csv(target_path, index=False, encoding="utf-8-sig")
        df.to_csv(latest_path, index=False, encoding="utf-8-sig")
        logger.info(f"CSV report archived to {target_path} and {latest_path}")
        return target_path

    def send_email_alert(self, subject: str, body: str) -> bool:
        """
        Extensible Email alert stub for future outbound delivery.
        """
        email_cfg = self.config.output.email
        if not email_cfg.enabled or not email_cfg.recipients:
            return False

        try:
            import smtplib
            from email.mime.text import MIMEText

            msg = MIMEText(body, "html", "utf-8")
            msg["Subject"] = subject
            msg["From"] = email_cfg.sender
            msg["To"] = ", ".join(email_cfg.recipients)

            if email_cfg.use_ssl:
                server = smtplib.SMTP_SSL(email_cfg.smtp_server, email_cfg.smtp_port, timeout=10)
            else:
                server = smtplib.SMTP(email_cfg.smtp_server, email_cfg.smtp_port, timeout=10)

            server.login(email_cfg.sender, email_cfg.password)
            server.sendmail(email_cfg.sender, email_cfg.recipients, msg.as_string())
            server.quit()
            logger.info("Email alert dispatched successfully.")
            return True
        except Exception as e:
            logger.warning(f"Failed to send email alert: {e}")
            return False
