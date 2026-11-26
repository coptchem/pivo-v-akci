from chains import leaflet_graph as lg


def offer(**kw):
    base = {"name": "Gambrinus 10 0,5 l", "price": 14.9, "old_price": 21.9,
            "volume": "0,5 l", "price_per_l": 29.8, "note": None,
            "valid_from": "2026-11-25", "valid_to": "2026-12-01"}
    base.update(kw)
    return base


def run(client):
    return lg.run_page(lg.build_graph(client), b"jpeg", "Albert", 2026)


# ------------------------------------------------------------ check_offer

def test_valid_offer_has_no_issues():
    assert lg.check_offer(offer(), 2026) == []


def test_multipack_per_litre_is_consistent():
    o = offer(name="Pilsner 6× 0,5 l", price=149.9, old_price=199.9,
              volume="6× 0,5 l", price_per_l=49.97)
    assert lg.check_offer(o, 2026) == []


def test_per_litre_mismatch_is_flagged():
    codes = [c for c, _ in lg.check_offer(offer(price_per_l=14.9), 2026)]
    assert codes == ["price_per_l"]


def test_old_price_not_higher_is_flagged():
    codes = [c for c, _ in lg.check_offer(offer(old_price=12.0), 2026)]
    assert codes == ["old_price"]


def test_reversed_dates_are_flagged():
    o = offer(valid_from="2026-12-05", valid_to="2026-12-01")
    assert [c for c, _ in lg.check_offer(o, 2026)] == ["dates"]


def test_impossible_price_short_circuits():
    assert [c for c, _ in lg.check_offer(offer(price=0), 2026)] == ["price"]


HEFFRON = dict(name="Heffron Degustační sada", price=349.0, old_price=None,
               volume="3× 0,2 l", price_per_l=581.67)


def test_expensive_per_litre_is_not_beer():
    codes = [c for c, _ in lg.check_offer(offer(**HEFFRON), 2026)]
    assert codes == ["not_beer"]


def test_not_beer_name_is_flagged():
    codes = [c for c, _ in lg.check_offer(offer(name="Kingswood cider"), 2026)]
    assert codes == ["not_beer"]


def test_premium_beer_is_not_flagged():
    o = offer(name="Bernard dárkové balení 4× 0,5 l", price=219.9,
              old_price=259.9, volume="4× 0,5 l", price_per_l=109.95)
    assert lg.check_offer(o, 2026) == []


# ------------------------------------------------------------ graf

def test_not_beer_dropped_after_retry(fake_client):
    client = fake_client([[offer(), offer(**HEFFRON)], [offer()]])
    state = run(client)
    assert state["attempts"] == 2
    assert state["offers"] == [offer()]
    assert "neobvyklé" in client.requests[1]["messages"][-1]["content"]


def test_not_beer_dropped_by_repair(fake_client):
    bad = [offer(), offer(**HEFFRON)]
    state = run(fake_client([bad, bad, bad]))
    assert state["offers"] == [offer()]
    assert state["review"][0]["action"] == "zahozeno"


def test_clean_page_needs_one_call(fake_client):
    client = fake_client([[offer()]])
    state = run(client)
    assert state["attempts"] == 1
    assert state["offers"] == [offer()]
    assert not state.get("review")


def test_retry_sends_feedback_and_accepts_fix(fake_client):
    client = fake_client([[offer(price_per_l=14.9)], [offer()]])
    state = run(client)
    assert state["attempts"] == 2
    assert state["offers"] == [offer()]
    second = client.requests[1]["messages"]
    assert second[-2]["role"] == "assistant"
    assert "cena za litr 14.9" in second[-1]["content"]
    assert client.requests[1]["model"] == lg.BASE_MODEL


def test_escalates_to_strong_model(fake_client):
    bad = [offer(price_per_l=14.9)]
    client = fake_client([bad, bad, [offer()]])
    state = run(client)
    assert [r["model"] for r in client.requests] == [
        lg.BASE_MODEL, lg.BASE_MODEL, lg.STRONG_MODEL]
    assert state["offers"] == [offer()]


def test_repair_after_all_attempts(fake_client):
    bad = [offer(price_per_l=14.9), offer(name="Kozel 11", price=0)]
    client = fake_client([bad, bad, bad])
    state = run(client)
    assert state["attempts"] == 3
    # nesmyslná cena zahozena, cena za litr odstraněna (dopočítá se z objemu)
    assert state["offers"] == [offer(price_per_l=None)]
    assert [r["action"] for r in state["review"]] == [
        "opraveno pravidly", "zahozeno"]


def test_unusable_reply_is_retried(fake_client):
    client = fake_client([("max_tokens", []), [offer()]])
    state = run(client)
    assert state["attempts"] == 2
    assert state["offers"] == [offer()]


def test_extract_pages_collects_report(fake_client):
    lg.drain_report()
    client = fake_client([[offer()]])
    assert lg.extract_pages(client, [b"p1"], "Albert", 2026) == [offer()]
    review, calls = lg.drain_report()
    assert review == [] and len(calls) == 1
