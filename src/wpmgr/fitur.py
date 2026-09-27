"""Nama fitur yang diumumkan connector lewat /ping dan /inventory.

Dashboard hanya menjadwalkan pekerjaan untuk fitur yang diumumkan. Connector
lama tidak punya endpoint baru, dan WordPress membalasnya dengan 404
`rest_no_route`, yang oleh klasifikasi Lapis 1 dibaca sebagai "connector
hilang".
"""

EVENTS = "events"
TRAFFIC = "traffic"
SELF_UPDATE = "self_update"
STAGING = "staging"


def punya_fitur(site, nama: str) -> bool:
    return nama in (site.fitur or [])
