import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from metrics import compare, same_offer, summarize  # noqa: E402

E = {"name": "Pilsner Urquell 0,5 l", "price": 24.9, "old_price": 34.9,
     "volume": "0,5 l", "price_per_l": 49.8}


def test_same_offer_tolerates_name_variants():
    assert same_offer(E, {"name": "PILSNER URQUELL světlý ležák", "price": 24.9})
    assert not same_offer(E, {"name": "Pilsner Urquell", "price": 26.9})
    assert not same_offer(E, {"name": "Kozel 11", "price": 24.9})


def test_same_offer_name_contained():
    e = {"name": "Cecilia 11 bezlepkový ležák", "price": 18.9}
    assert same_offer(e, {"name": "Cecilia 11", "price": 18.9})
    assert not same_offer(e, {"name": "Ce", "price": 18.9})


def test_same_offer_joint_offer():
    e = {"name": "Mustang světlý ležák / Černá Barbora speciální tmavé pivo",
         "price": 16.9}
    assert same_offer(e, {"name": "Mustang", "price": 16.9})
    assert same_offer(e, {"name": "Mustang / Černá Barbora", "price": 16.9})
    assert not same_offer(e, {"name": "Svijany", "price": 16.9})


def test_compare_counts_and_fields():
    predicted = [
        dict(E, old_price=None),                       # spárováno, pův. cena chybí
        {"name": "Radegast 12", "price": 19.9},        # navíc
    ]
    expected = [E, {"name": "Bernard 11", "price": 21.9}]
    row = compare(expected, predicted)
    assert (row["expected"], row["predicted"], row["matched"]) == (2, 2, 1)
    assert row["fields"] == {"volume": 1, "old_price": 0, "price_per_l": 1}
    assert row["missed"] == ["Bernard 11"]
    s = summarize([row])
    assert s["precision"] == 0.5 and s["recall"] == 0.5
