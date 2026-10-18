"""The message catalogue, keywords and sending rules without a database (R2-1C; ADR-0043,
ADR-0044; FR-MSG-001, FR-MSG-005, FR-MSG-006, FR-MSG-009..011, FR-MSG-019, FR-LANG-005)."""

from __future__ import annotations

import datetime as dt
import re
from importlib import resources

import pytest
import yaml
from pydantic import ValidationError

from app.core import languages
from app.core.config import DeploymentMode, Environment, MessagingProviderMode, Settings
from app.messaging import catalogue as cat
from app.messaging import eligibility as rules
from app.messaging import slips
from app.messaging.config import LANGUAGES, classify, load_config
from app.messaging.providers import get_provider
from app.messaging.providers.fake import FakeMessagingProvider

TODAY = dt.date(2026, 10, 17)
INR_FROM = dt.date(2027, 1, 1)


def test_FR_MSG_005_catalogue_is_utility_only() -> None:
    templates = cat.catalogue().templates
    assert set(templates) == {
        "optin_confirm",
        "school_notice",
        "absence_alert",
        "fee_reminder",
        "fee_receipt",
    }
    assert {t.category for t in templates.values()} == {"utility"}


def test_FR_MSG_005_a_marketing_template_is_refused_at_load() -> None:
    raw = yaml.safe_load(
        resources.files("app.messaging").joinpath("templates.yaml").read_text("utf-8")
    )
    raw["templates"]["school_notice"]["category"] = "marketing"
    with pytest.raises(ValidationError, match="utility"):
        cat.Catalogue.model_validate(raw)


def test_FR_MSG_005_bodies_use_every_variable_and_never_start_or_end_with_one() -> None:
    for key, item in cat.catalogue().templates.items():
        for language in ("en", "te"):
            body = item.text(language).body
            numbers = sorted(int(n) for n in re.findall(r"\{\{(\d+)\}\}", body))
            assert numbers == list(range(1, len(item.variables) + 1)), (key, language)
            assert not body.startswith("{{")
            assert not body.rstrip(".").endswith("}}")


def test_PRV_026_variables_carry_only_what_the_purpose_needs() -> None:
    allowed = {
        "school_name",
        "notice_title",
        "child_first_name",
        "date",
        "due_date",
        "amount",
        "receipt_number",
    }
    for item in cat.catalogue().templates.values():
        assert {v.name for v in item.variables} <= allowed


def test_FR_MSG_006_deterministic_provider_names() -> None:
    assert cat.provider_name("school_notice", 1, "te") == "sos_school_notice_v1_te"


def test_D32_no_template_offers_an_opt_out_button_or_stop_text() -> None:
    """Owner decision D32 (2026-10-18): utility messages only, so no "Stop messages" button and
    no "reply STOP" text in any template; inbound STOP is still honoured (FR-MSG-010)."""
    for item in cat.catalogue().templates.values():
        assert item.buttons in ((), ("url",))
        for language in LANGUAGES:
            body = item.text(language).body
            assert "STOP" not in body
            assert "ఆపు" not in body
        components = cat.build_components(item.purpose, "en", "https://my.schoolos.example")
        kinds = [b["type"] for c in components if c["type"] == "BUTTONS" for b in c["buttons"]]
        assert "QUICK_REPLY" not in kinds
    for language in LANGUAGES:
        assert "stop_note" not in cat.catalogue().slip[language].model_dump()


def test_FR_MSG_006_url_button_needs_the_public_origin() -> None:
    with pytest.raises(ValueError, match="SOS_PUBLIC_BASE_URL"):
        cat.build_components("school_notice", "en", None)
    components = cat.build_components("school_notice", "en", "https://my.schoolos.example")
    url = components[1]["buttons"][0]
    assert url["type"] == "URL"
    assert url["url"] == "https://my.schoolos.example/p/{{1}}"


def test_FR_LANG_005_unreviewed_telugu_texts_are_listed_for_the_school() -> None:
    unreviewed = cat.unreviewed("te")
    assert "messaging.template.school_notice" in unreviewed
    assert cat.unreviewed("en") == []
    # app/core/parent_texts.yaml (the school settings' list) names every one of them.
    assert set(unreviewed) <= set(languages.unreviewed_parent_texts("te"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("STOP", ("stop", None)),
        ("  stop. ", ("stop", None)),
        ("Unsubscribe", ("stop", None)),
        ("ఆపు", ("stop", None)),
        ("JOIN ABCD2345", ("join", "ABCD2345")),
        ("join abcd-2345", ("join", "ABCD2345")),
        ("చేరండి ABCD2345", ("join", "ABCD2345")),
        ("JOIN", ("join", None)),
        ("JOIN 0OIL1234", ("join", None)),
        ("When is the exam?", ("other", None)),
        ("", ("other", None)),
    ],
)
def test_FR_MSG_010_keywords(text: str, expected: tuple[str, str | None]) -> None:
    assert classify(text) == expected


def test_FR_MSG_010_stop_button_payload() -> None:
    assert classify(None, load_config().keywords.stop_button_payload) == ("stop", None)


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("9000012345", "919000012345"),
        ("6000012345", "916000012345"),
        ("5000012345", None),
        ("900001234", None),
        (None, None),
    ],
)
def test_FR_MSG_019_phone_format(phone: str | None, expected: str | None) -> None:
    assert rules.whatsapp_number(phone) == expected


def test_FR_MSG_019_inbound_sender_numbers() -> None:
    assert rules.mobile_from_wa_id("919000012345") == "9000012345"
    assert rules.mobile_from_wa_id("4479000012345") is None
    assert rules.mobile_from_wa_id(None) is None


ACTIVE = rules.AccountState("active", "INR")
APPROVED = rules.TemplateState("approved", "UTILITY", False)
IN = rules.OptinState("opted_in", True)


def _reason(**kw: object) -> str | None:
    args: dict[str, object] = {
        "account": ACTIVE,
        "template": APPROVED,
        "needs_template": True,
        "optin": IN,
        "needs_optin": True,
        "phone": "9000012345",
        "today": TODAY,
        "inr_required_from": INR_FROM,
    }
    args.update(kw)
    return rules.skip_reason(**args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({}, None),
        ({"account": None}, "messaging_not_connected"),
        ({"account": rules.AccountState("disconnected", "INR")}, "messaging_not_connected"),
        ({"account": rules.AccountState("paused", "INR")}, "messaging_paused"),
        (
            {"account": rules.AccountState("active", "USD"), "today": dt.date(2027, 1, 1)},
            "messaging_currency_not_inr",
        ),
        ({"account": rules.AccountState("active", "USD")}, None),
        ({"template": None}, "template_not_approved"),
        ({"template": rules.TemplateState("pending", "UTILITY", False)}, "template_not_approved"),
        ({"template": rules.TemplateState("rejected", "UTILITY", False)}, "template_not_approved"),
        ({"template": rules.TemplateState("approved", "MARKETING", True)}, "template_paused"),
        ({"template": rules.TemplateState("paused", "UTILITY", False)}, "template_paused"),
        ({"template": None, "needs_template": False}, None),
        ({"optin": None}, "no_optin"),
        ({"optin": rules.OptinState("opted_out", False)}, "opted_out"),
        ({"optin": rules.OptinState("opted_in", False)}, "optin_for_other_number"),
        ({"optin": None, "needs_optin": False}, None),
        ({"phone": None}, "no_mobile_number"),
        ({"phone": "1234567890"}, "no_mobile_number"),
    ],
)
def test_FR_MSG_011_eligibility_matrix(change: dict[str, object], reason: str | None) -> None:
    assert _reason(**change) == reason


def test_FR_MSG_021_default_daily_cap_is_meta_limit_minus_ten_percent() -> None:
    cfg = load_config()
    assert cfg.default_daily_cap("TIER_250") == 225
    assert cfg.default_daily_cap("TIER_1K") == 900
    assert cfg.default_daily_cap(None) is None


def test_FR_MSG_001_fake_provider_refused_in_production() -> None:
    with pytest.raises(ValueError, match="fake"):
        Settings(
            env=Environment.PROD,
            messaging_enabled=True,
            messaging_provider_mode=MessagingProviderMode.FAKE,
        )
    local = Settings(env=Environment.CI)
    assert local.effective_messaging_provider_mode is MessagingProviderMode.FAKE
    assert isinstance(get_provider(local), FakeMessagingProvider)


def test_FR_MSG_024_messaging_refused_on_a_dedicated_host() -> None:
    with pytest.raises(ValueError, match="dedicated"):
        Settings(
            env=Environment.CI, deployment_mode=DeploymentMode.DEDICATED, messaging_enabled=True
        )


def test_FR_MSG_009_join_slip_page() -> None:
    page = slips.render(
        school_name="Synthetic <Model> School",
        whatsapp_number="+91 90000 01234",
        slips=[
            slips.Slip(
                language="te",
                student_name="Synthetica Student",
                class_section="IX-A",
                code="ABCD2345",
                categories=("notices", "fees"),
                valid_until=TODAY,
            )
        ],
    )
    assert "Synthetic &lt;Model&gt; School" in page
    assert "JOIN ABCD2345" in page
    assert "https://wa.me/919000001234?text=JOIN%20ABCD2345" in page
    assert "<svg" in page
    assert "<script" not in page
    assert 'lang="te"' in page
    assert languages.contains_telugu(page)
