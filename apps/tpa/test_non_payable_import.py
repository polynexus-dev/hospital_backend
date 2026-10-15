"""MedNet M2.22 pharmacy setup: Excel / CSV upload of items not covered by
insurance into a non-payable list."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.xlsx import SpreadsheetError, build_xlsx, read_xlsx
from apps.finance.models import ServiceTariff
from apps.pharmacy.models import Medicine
from apps.tpa.models import NonPayableItem, NonPayableList

L = "/api/v1/tpa/non-payable-lists/"


@pytest.fixture
def npl(hospital):
    return NonPayableList.objects.create(hospital=hospital, name="IRDAI non-payable")


@pytest.fixture
def catalogue(hospital):
    return {
        "pan": Medicine.objects.create(hospital=hospital, name="Pan 40"),
        "nursing": ServiceTariff.objects.create(hospital=hospital, code="NURS-GEN", name="Nursing charges", rate=800),
    }


def xlsx_file(rows, name="items.xlsx"):
    return SimpleUploadedFile(name, build_xlsx(rows), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def test_reader_round_trips_the_writer():
    rows = [["Item", "Type", "Group"], ["Gloves", "keyword", "I"], [12, "", 3.5]]
    assert read_xlsx(build_xlsx(rows)) == [["Item", "Type", "Group"], ["Gloves", "keyword", "I"], ["12", "", "3.5"]]


def test_reader_refuses_non_xlsx_and_dtds():
    with pytest.raises(SpreadsheetError):
        read_xlsx(b"not a zip")
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><worksheet/>')
    with pytest.raises(SpreadsheetError):
        read_xlsx(buf.getvalue())


def test_import_matches_medicines_services_and_keywords(auth_client, npl, catalogue):
    rows = [["Item", "Type", "Group", "Note"],
            ["Pan 40", "", "IV", ""],
            ["NURS-GEN", "service", "II", "Part of room rent"],
            ["Gloves (sterile)", "", "I", "Consumable"],
            ["Unknown drug", "medicine", "I", ""],
            ["Belt", "", "Z", ""]]
    r = auth_client.post(f"{L}{npl.pk}/import/", {"file": xlsx_file(rows)}, format="multipart")
    assert r.status_code == 200, r.data
    assert r.data["added"] == 3 and r.data["total_items"] == 3
    assert {e["item"] for e in r.data["errors"]} == {"Unknown drug", "Belt"}
    assert NonPayableItem.objects.get(non_payable_list=npl, medicine=catalogue["pan"]).group == "list_4"
    assert NonPayableItem.objects.get(non_payable_list=npl, tariff=catalogue["nursing"]).note == "Part of room rent"
    assert NonPayableItem.objects.filter(non_payable_list=npl, keyword="Gloves (sterile)", group="list_1").exists()
    again = auth_client.post(f"{L}{npl.pk}/import/", {"file": xlsx_file(rows)}, format="multipart")
    assert again.data["added"] == 0 and again.data["already_on_list"] == 3  # no duplicates


def test_csv_import_with_replace(auth_client, npl):
    NonPayableItem.objects.create(hospital=npl.hospital, non_payable_list=npl, keyword="old item")
    csv = SimpleUploadedFile("items.csv", b"Item,Type,Group\nCotton,keyword,I\nMask,keyword,1\n", content_type="text/csv")
    r = auth_client.post(f"{L}{npl.pk}/import/", {"file": csv, "replace": "1"}, format="multipart")
    assert r.status_code == 200 and r.data["added"] == 2
    assert sorted(npl.items.values_list("keyword", flat=True)) == ["Cotton", "Mask"]


def test_import_template_downloads(auth_client):
    r = auth_client.get(f"{L}import-template/")
    assert r.status_code == 200 and read_xlsx(r.content)[0] == ["Item", "Type", "Group", "Note"]


def test_bad_file_is_a_400(auth_client, npl):
    r = auth_client.post(f"{L}{npl.pk}/import/", {"file": SimpleUploadedFile("x.xlsx", b"junk")}, format="multipart")
    assert r.status_code == 400
