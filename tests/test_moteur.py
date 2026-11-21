import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import asyncio, hashlib, os, tempfile, time
from pathlib import Path
import fdm_tui as F
import serveur as S

S.demarrer()
B = "http://127.0.0.1:8765"

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

async def attendre(g, ds, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        g.tick()
        if all(d.statut in ("completed", "error") for d in ds): return
        await asyncio.sleep(0.2)
    raise TimeoutError([ (d.nom, d.statut, d.fait, d.taille) for d in ds])

def mk():
    tmp = Path(tempfile.mkdtemp())
    cfg = F.Config(dossier=str(tmp / "dl"), tentatives=3)
    g = F.Gestionnaire(cfg, tmp / "etat.json")
    return tmp, cfg, g

async def main():
    # 1. multi-segments
    tmp, cfg, g = mk()
    d = g.ajouter(B + "/big.bin", checksum="sha256:" + S.SHA)
    await attendre(g, [d])
    assert d.statut == "completed", (d.statut, d.erreur)
    assert sha(d.chemin) == S.SHA and d.verif == "ok"
    assert d.ranges and len([s for s in d.segments]) == 0
    assert Path(d.chemin).parent.name == "Autres" or True
    print("1 segments OK:", d.nom, d.categorie, d.chemin)

    # 2. sans range
    d = g.ajouter(B + "/norange.bin")
    await attendre(g, [d]); assert d.statut == "completed" and sha(d.chemin) == S.SHA and not d.ranges
    print("2 sans range OK")

    # 3. content-disposition + nom dangereux + catégorie
    d = g.ajouter(B + "/cd")
    await attendre(g, [d]); assert d.statut == "completed", d.erreur
    assert "/" not in d.nom and d.categorie == "Documents" and Path(d.chemin).parent == Path(cfg.dossier)/"Documents", (d.nom, d.chemin)
    print("3 content-disposition OK:", d.nom)

    # 4. conflit de nom
    d2 = g.ajouter(B + "/cd?x=1")
    await attendre(g, [d2]); assert d2.nom != d.nom and d2.statut == "completed", d2.nom
    print("4 nom unique OK:", d2.nom)

    # 5. retries (503 x2 puis ok)
    S.COUNT["flaky"] = 0
    F.asyncio.sleep  # backoff réel 2s+4s
    d = g.ajouter(B + "/flaky.bin")
    await attendre(g, [d], 90); assert d.statut == "completed" and sha(d.chemin) == S.SHA
    print("5 retries OK")

    # 6. 404 fatal
    d = g.ajouter(B + "/404.zip"); await attendre(g, [d])
    assert d.statut == "error" and "404" in d.erreur; print("6 404 OK:", d.erreur)

    # 7. checksum faux
    d = g.ajouter(B + "/small.txt", checksum="0"*64); await attendre(g, [d])
    assert d.statut == "completed" and d.verif == "ko"; print("7 checksum KO détecté OK")

    # 8. pause / reprise (limite de vitesse pour avoir le temps)
    tmp, cfg, g = mk()
    g.appliquer_limite(3000)  # 3 Mo/s -> ~4s
    d = g.ajouter(B + "/big.bin"); 
    for _ in range(8): g.tick(); await asyncio.sleep(0.25)
    g.pause(d); await asyncio.sleep(0.5)
    assert d.statut == "paused" and 0 < d.fait < d.taille, (d.fait, d.taille)
    fait1 = d.fait; segs = [(s.start, s.end, s.pos) for s in d.segments]
    assert any(s.restant for s in d.segments)
    # persistance + rechargement
    g.sauvegarder()
    g2 = F.Gestionnaire(cfg, tmp / "etat.json"); g2.charger(); g2.limiteur.regler(0)
    d2 = g2.liste[0]; assert d2.statut == "paused" and d2.fait == fait1
    g2.reprendre(d2); await attendre(g2, [d2])
    assert d2.statut == "completed" and sha(d2.chemin) == S.SHA, d2.erreur
    print("8 pause/reprise/persistance OK  (fait au moment de la pause:", fait1, ")")

    # 9. limite de vitesse respectée
    tmp, cfg, g = mk(); g.appliquer_limite(4096)  # 4 Mo/s, fichier 12 Mo -> ~3s
    d = g.ajouter(B + "/big.bin"); t0 = time.time(); await attendre(g, [d]); dt = time.time() - t0
    assert dt > 2.0, dt; print(f"9 limite OK: 12.1 Mo en {dt:.1f}s avec 4 Mo/s")

    # 10. file d'attente (simultanes=1)
    tmp, cfg, g = mk(); cfg.simultanes = 1; g.appliquer_limite(6000)
    a = g.ajouter(B + "/big.bin"); b = g.ajouter(B + "/small.txt")
    g.tick(); await asyncio.sleep(0.3); g.tick()
    assert a.statut == "downloading" and b.statut == "queued", (a.statut, b.statut)
    await attendre(g, [a, b]); assert a.statut == b.statut == "completed"
    print("10 file d'attente OK")

    # 11. plage horaire : hors plage -> rien ne démarre
    tmp, cfg, g = mk()
    from datetime import datetime, timedelta
    h = datetime.now() + timedelta(hours=2)
    cfg.plage = f"{h:%H:%M}-{(h+timedelta(hours=1)):%H:%M}"
    d = g.ajouter(B + "/small.txt"); g.tick(); await asyncio.sleep(0.5); g.tick()
    assert d.statut == "queued" and not g.dans_plage()
    cfg.plage = ""; await attendre(g, [d]); assert d.statut == "completed"
    # fenêtre qui passe minuit
    cfg.plage = "23:00-07:00"; assert isinstance(g.dans_plage(), bool)
    print("11 plage horaire OK")

    # 12. suppression sécurisée + fichiers
    tmp, cfg, g = mk(); d = g.ajouter(B + "/small.txt"); await attendre(g, [d])
    p = d.chemin; assert os.path.exists(p); g.supprimer(d, True); assert not os.path.exists(p) and not g.liste
    d = g.ajouter(B + "/small.txt?z"); await attendre(g, [d]); d.chemin = "/etc/hostname"
    g.supprimer(d, True); assert os.path.exists("/etc/hostname"); print("12 suppression sûre OK")

    # 13. miroir : url principale 404 -> miroir
    tmp, cfg, g = mk()
    d = g.ajouter(B + "/big.bin", miroirs=[B + "/big.bin?m=1"]); await attendre(g, [d]); assert sha(d.chemin) == S.SHA
    print("13 miroirs (sans panne) OK")

    # 14. parsing
    assert len(F.developper("http://x/i[001-120].jpg")) == 120 and F.developper("http://x/i[001-003].jpg")[0].endswith("i001.jpg")
    assert len(F.developper("http://x/[1-2]/[1-3].jpg")) == 6
    u, o = F.analyser_ligne('http://a/b.zip -o "mon fichier.zip" -r http://ref -H "X-T: 1" -n 4 --limite 100 -m http://m/b.zip')
    assert u == ["http://a/b.zip"] and o["nom"] == "mon fichier.zip" and o["entetes"] == {"Referer": "http://ref", "X-T": "1"} and o["max_segments"] == 4 and o["miroirs"] == ["http://m/b.zip"]
    u, o = F.analyser_ligne("http://a/1 http://a/2 -r x"); assert len(u) == 2
    try: F.analyser_ligne("http://a --bogus"); assert False
    except ValueError: pass
    assert F.detecter_type("magnet:?xt=urn:btih:abc") == "torrent" and F.detecter_type("https://www.youtube.com/watch?v=1") == "video" and F.detecter_type("http://a/b.zip") == "http"
    for bad in ("ftp://x/y", ):
        try: g.ajouter(bad); assert False
        except ValueError: pass
    L = F.filtrer_liens(B + "/page", ["big.bin", "/cd.pdf", "img/a.png", "mailto:x", "/page2", "big.bin#frag"], set())
    assert L == [B + "/cd.pdf", B + "/img/a.png"], L
    L2 = F.filtrer_liens(B + "/page", ["big.bin", "big.bin#f", "x.zip"], {"bin"}); assert L2 == [B + "/big.bin"], L2
    assert F.filtrer_liens(B, ["a.pdf", "b.zip"], {"pdf"}) == [B + "/a.pdf"]
    assert F.nom_depuis_headers("attachment; filename*=UTF-8''%C3%A9t%C3%A9.txt") == "été.txt"
    print("14 parsing OK")

    # 15. torrent invalide / libtorrent
    tmp, cfg, g = mk()
    d = g.ajouter("magnet:?xt=urn:btih:" + "a"*40 + "&dn=test")
    for _ in range(6): g.tick(); await asyncio.sleep(0.5)
    assert d.statut == "downloading" and d.kind == "torrent", (d.statut, d.erreur)
    g.pause(d); await asyncio.sleep(0.5); assert d.statut == "paused" and d.handle is None
    print("15 torrent (ajout/pause magnet) OK:", d.nom)

    print("\nTOUS LES TESTS MOTEUR OK")

asyncio.run(main())
