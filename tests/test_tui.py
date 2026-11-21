import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import asyncio, tempfile
from pathlib import Path
import fdm_tui as F
import serveur as S
from textual.widgets import DataTable, TextArea, Input, Static

S.demarrer(8766)
B = "http://127.0.0.1:8766"

async def main():
    tmp = Path(tempfile.mkdtemp())
    cfg = F.Config(dossier=str(tmp/"dl"))
    F.CONFIG_DIR = tmp
    g = F.Gestionnaire(cfg, tmp/"etat.json")
    app = F.FdmApp(cfg, g, f"{B}/small.txt\n{B}/cd.pdf -o 'Film [1080p].pdf'\nftp://bad/x")
    async with app.run_test(size=(150, 45)) as pilot:
        await pilot.pause(0.5)
        t = app.query_one(DataTable)
        assert t.row_count == 2, t.row_count
        for _ in range(30):
            await pilot.pause(0.3)
            if all(d.statut == "completed" for d in g.liste): break
        assert all(d.statut == "completed" for d in g.liste), [(d.statut, d.erreur) for d in g.liste]
        await pilot.pause(1.2)
        row = [str(c) for c in t.get_row_at(1)]
        print("ligne:", row)
        assert "Film [1080p].pdf" in row[1] and "Terminé" in row[6]

        # modal ajout
        await pilot.press("a"); await pilot.pause(0.3)
        assert len(app.screen_stack) == 2
        ta = app.screen.query_one(TextArea); assert ta.has_focus
        ta.text = f"{B}/big.bin -n 4\n{B}/norange.bin"
        await pilot.press("ctrl+s"); await pilot.pause(0.5)
        assert len(app.screen_stack) == 1 and t.row_count == 4, (len(app.screen_stack), t.row_count)
        # escape dans le modal
        await pilot.press("a"); await pilot.pause(0.3); await pilot.press("escape"); await pilot.pause(0.3)
        assert len(app.screen_stack) == 1
        # raccourcis non déclenchés dans un modal : taper 'p' dans le TextArea
        await pilot.press("a"); await pilot.pause(0.3); await pilot.press("p", "d"); await pilot.pause(0.2)
        assert len(app.screen_stack) == 2 and app.screen.query_one(TextArea).text == "pd"
        await pilot.press("escape"); await pilot.pause(0.3)

        for _ in range(60):
            await pilot.pause(0.3)
            if all(d.statut in ("completed","error") for d in g.liste): break
        print("statuts:", [(d.id, d.statut, d.erreur) for d in g.liste])
        assert all(d.statut == "completed" for d in g.liste)

        # filtre
        await pilot.press("t"); await pilot.pause(0.2); await pilot.press("t"); await pilot.pause(0.2)
        assert app.filtre == 2 and t.row_count == 0
        for _ in range(3): await pilot.press("t")
        await pilot.pause(0.3); assert app.filtre == 0 and t.row_count == 4

        # déplacer [ ] 
        t.move_cursor(row=1); await pilot.pause(0.1)
        ident = app._courant().id
        await pilot.press("left_square_bracket"); await pilot.pause(0.3)
        assert g.liste[0].id == ident, [d.id for d in g.liste]
        await pilot.press("right_square_bracket"); await pilot.pause(0.3)
        assert g.liste[1].id == ident

        # détails
        await pilot.press("i"); await pilot.pause(0.5)
        assert len(app.screen_stack) == 2
        txt = str(app.screen.query_one("#contenu", Static).render()); assert "Carte des segments" in txt, txt
        await pilot.press("escape"); await pilot.pause(0.3); assert len(app.screen_stack) == 1
        # aide
        await pilot.press("h"); await pilot.pause(0.3); assert len(app.screen_stack) == 2
        await pilot.press("escape"); await pilot.pause(0.3)
        # limite
        await pilot.press("l"); await pilot.pause(0.3)
        app.screen.query_one(Input).value = "500"; await pilot.press("enter"); await pilot.pause(0.3)
        assert cfg.limite_ko == 500 and g.limiteur.rate == 512000
        await pilot.press("l"); await pilot.pause(0.3)
        app.screen.query_one(Input).value = "0"; await pilot.press("enter"); await pilot.pause(0.3)
        # réglages
        await pilot.press("s"); await pilot.pause(0.4)
        app.screen.query_one("#r-simultanes", Input).value = "5"
        await pilot.press("ctrl+s"); await pilot.pause(0.4)
        assert cfg.simultanes == 5 and len(app.screen_stack) == 1
        await pilot.press("s"); await pilot.pause(0.4)
        app.screen.query_one("#r-plage", Input).value = "bad"
        await pilot.press("ctrl+s"); await pilot.pause(0.3); assert len(app.screen_stack) == 2  # refus
        await pilot.press("escape"); await pilot.pause(0.3)
        # action de fin / presse-papiers toggles
        await pilot.press("e"); await pilot.pause(0.2); assert cfg.action_fin == "quitter"
        await pilot.press("e","e","e"); await pilot.pause(0.2); assert cfg.action_fin == "aucune"
        # retirer + supprimer fichiers
        n = t.row_count; await pilot.press("d"); await pilot.pause(0.3); assert t.row_count == n-1
        d = app._courant(); path = d.chemin
        await pilot.press("D"); await pilot.pause(0.3); assert len(app.screen_stack) == 2
        await pilot.click("#oui"); await pilot.pause(0.4)
        assert t.row_count == n-2 and not Path(path).exists()
        # vider terminés
        await pilot.press("C"); await pilot.pause(0.3); assert t.row_count == 0
        # bouton Ajouter
        await pilot.click("#b-ajouter"); await pilot.pause(0.3); assert len(app.screen_stack) == 2
        await pilot.press("escape"); await pilot.pause(0.3)
        # aspirer une page
        await pilot.press("g"); await pilot.pause(0.3)
        app.screen.query_one(Input).value = f"{B}/page pdf,png"; await pilot.press("enter"); await pilot.pause(1.0)
        assert len(app.screen_stack) == 2, "confirm attendu"
        await pilot.click("#oui"); await pilot.pause(0.5)
        print("après aspiration:", [d.url for d in g.liste])
        assert len(g.liste) == 2
        # statut etat
        print("barre:", app.query_one("#etat", Static).render())
        await pilot.press("q")
    # état sauvegardé
    g2 = F.Gestionnaire(cfg, tmp/"etat.json"); g2.charger(); print("rechargé:", len(g2.liste))
    print("TUI OK")

asyncio.run(main())
