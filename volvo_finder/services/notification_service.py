"""Notification service for Telegram and email alerts."""
import asyncio
import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

import aiohttp

from models.car import Car

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Sends notifications about new car listings via:
    - Telegram (if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set)
    - Email/SMTP (if SMTP settings are configured)
    """

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    async def send_telegram(self, message: str) -> bool:
        """
        Send message via Telegram bot.
        Returns True on success, False on failure.
        """
        if not self.settings.TELEGRAM_BOT_TOKEN or not self.settings.TELEGRAM_CHAT_ID:
            logger.debug("Telegram not configured — skipping notification")
            return False

        url = f"https://api.telegram.org/bot{self.settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": self.settings.TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        logger.info(json.dumps({"event": "telegram_sent", "chat_id": self.settings.TELEGRAM_CHAT_ID}))
                        return True
                    else:
                        body = await resp.text()
                        logger.warning(json.dumps({
                            "event": "telegram_failed",
                            "status": resp.status,
                            "body": body[:200],
                        }))
                        return False
        except Exception as e:
            logger.error(json.dumps({"event": "telegram_error", "error": str(e)}))
            return False

    def send_email(self, subject: str, body: str) -> bool:
        """
        Send email via SMTP.
        Returns True on success, False if not configured or on failure.
        """
        if not all([
            self.settings.SMTP_HOST,
            self.settings.SMTP_USER,
            self.settings.SMTP_PASSWORD,
            self.settings.NOTIFY_EMAIL,
        ]):
            logger.debug("Email not configured — skipping notification")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.settings.SMTP_USER
            msg["To"] = self.settings.NOTIFY_EMAIL

            part_plain = MIMEText(body, "plain", "utf-8")
            part_html = MIMEText(f"<pre>{body}</pre>", "html", "utf-8")
            msg.attach(part_plain)
            msg.attach(part_html)

            with smtplib.SMTP(self.settings.SMTP_HOST, self.settings.SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(self.settings.SMTP_USER, self.settings.SMTP_PASSWORD)
                server.sendmail(
                    self.settings.SMTP_USER,
                    self.settings.NOTIFY_EMAIL,
                    msg.as_string(),
                )

            logger.info(json.dumps({"event": "email_sent", "to": self.settings.NOTIFY_EMAIL}))
            return True

        except Exception as e:
            logger.error(json.dumps({"event": "email_error", "error": str(e)}))
            return False

    async def send_slack(self, blocks: list[dict]) -> bool:
        """
        Send a Block Kit message to Slack via Incoming Webhook.
        Returns True on success, False if not configured or on failure.
        """
        if not self.settings.SLACK_WEBHOOK_URL:
            logger.debug("Slack not configured — skipping notification")
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.settings.SLACK_WEBHOOK_URL,
                    json={"blocks": blocks},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        logger.info(json.dumps({"event": "slack_sent"}))
                        return True
                    body = await resp.text()
                    logger.warning(json.dumps({
                        "event": "slack_failed",
                        "status": resp.status,
                        "body": body[:200],
                    }))
                    return False
        except Exception as e:
            logger.error(json.dumps({"event": "slack_error", "error": str(e)}))
            return False

    def _car_to_slack_blocks(self, car: Car, label: str = "") -> list[dict]:
        """Build Slack Block Kit blocks for a single car listing."""
        features = ", ".join(sorted(car.features)) if car.features else "–"
        audio = f" | Ljud: {car.audio_system}" if car.audio_system else ""
        header_text = f"{label}*{car.model} {car.year}* — {car.price_sek:,} kr"
        body_text = (
            f"Mil: {car.mileage_mil:.0f} mil | Bränsle: {car.fuel}{audio}\n"
            f"Utrustning: {features}\n"
            f"Källa: {car.source} | Poäng: {car.score:.1f}"
        )
        return [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": header_text},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Se annons"},
                    "url": car.url,
                    "action_id": f"view_{car.source}",
                },
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": body_text}},
            {"type": "divider"},
        ]

    async def send_all_cars_to_slack(self, cars: list[Car]) -> None:
        """
        Send all found cars to Slack, one message per car with a direct link.
        Batches into groups of 10 to stay within Slack's block limits.
        """
        if not cars:
            return

        header_blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚗 VolvoFinder — {len(cars)} annonser hittades",
                },
            },
            {"type": "divider"},
        ]
        await self.send_slack(header_blocks)

        batch_size = 10
        for i in range(0, len(cars), batch_size):
            batch = cars[i : i + batch_size]
            blocks: list[dict] = []
            for car in batch:
                blocks.extend(self._car_to_slack_blocks(car))
            await self.send_slack(blocks)

        logger.info(json.dumps({"event": "slack_all_cars_sent", "count": len(cars)}))

    def _format_car_message(self, car: Car) -> str:
        """Format a single car for Telegram/email notification text."""
        features = ", ".join(sorted(car.features)) if car.features else "–"
        return (
            f"<b>{car.model} {car.year}</b> — {car.price_sek:,} kr\n"
            f"Mil: {car.mileage_mil:.0f} mil | Bränsle: {car.fuel}\n"
            f"Utrustning: {features}\n"
            f"Källa: {car.source}\n"
            f"<a href='{car.url}'>Se annons</a>"
        )

    async def notify_new_cars(self, new_cars: list[Car]) -> None:
        """Send Telegram + email notifications for newly discovered cars."""
        if not new_cars:
            return

        # Build Telegram message
        header = f"🚗 {len(new_cars)} ny/nya Volvo-annons(er) hittades!\n\n"
        car_messages = [self._format_car_message(c) for c in new_cars[:10]]
        full_message = header + "\n\n---\n\n".join(car_messages)
        if len(new_cars) > 10:
            full_message += f"\n\n...och {len(new_cars) - 10} till."

        telegram_result = await self.send_telegram(full_message)
        if telegram_result:
            logger.info(json.dumps({"event": "telegram_notification_sent", "count": len(new_cars)}))

        # Email
        plain_lines = [f"Ny Volvo-annons hittades! ({len(new_cars)} st)\n"]
        for car in new_cars[:10]:
            plain_lines.append(
                f"{car.model} {car.year} — {car.price_sek:,} kr — {car.mileage_mil:.0f} mil — {car.fuel}\n"
                f"  {car.url}\n"
            )
        email_result = self.send_email(
            subject=f"VolvoFinder: {len(new_cars)} ny/nya annonser",
            body="\n".join(plain_lines),
        )
        if email_result:
            logger.info(json.dumps({"event": "email_notification_sent", "count": len(new_cars)}))
