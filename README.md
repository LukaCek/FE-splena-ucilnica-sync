# FE Moodle Sync — beta 1.8.1

Enosmerno, samodejno arhiviranje **datotek iz Moodle map** [e.FE](https://ucilnica-fe.uni-lj.si) na osebni TrueNAS, kjer jih obstoječi FE Gradiva MCP lahko bere. Ločen servis, brez lokalnega AI ali Chromiuma v Dockerju.

**Stanje:** beta 1.5.1 je na TrueNAS-u 19. 10. 2026 uspešno preverila 11 predmetov,
354 shranjenih datotek, 19/19 lokalnih strani, 3 forumske objave in 2 prilogi.
Beta 1.6 dodaja novosti in koledar na osnovi preverljivih Moodle podatkov;
dostop do koledarskega API-ja na konkretni e.FE namestitvi je treba še preveriti.

## Kaj zmore

### Offline spletna učilnica v HTML

Po uspešni sinhronizaciji se samodejno ustvarijo pregledne, **povsem lokalne** HTML strani, po zgradbi podobne e.FE. Nova beta 1.2 prinaša UL|FE-podoben bel zgornji meni, rdečo navigacijo, manjše vrstice dejavnosti, zložljive Moodle razdelke in zložljive mape, iskanje, filtriranje lokalnih/spletnih dejavnosti ter povezave na originalni predmet. Razdelki se preberejo iz izvirnega HTML-ja, kadar so prepoznavni; za potrjeno Matematiko I je dodana omejena rezervna razvrstitev iz dejansko prikazanih modulov. Za ostale predmete razdelkov ne ugibamo:

- **Glavna stran:** `FE/Spletna-ucilnica/index.html` — vsi odkriti predmeti in iskanje po imenih.
- **Stran vsakega predmeta:** `FE/1-semester/01_MAT/Spletna-ucilnica/index.html` (za Matematiko I; ostali predmeti v svojih mapah) — razdelki in dejavnosti v zaporedju, kot jih prepozna Moodle, zložljive mape/podmape, PDF-ji in druge prenesene datoteke.
- **Lokalne povezave:** klik na datoteko uporablja relativno pot, zato mora delovati tudi po Syncthing prenosu na prenosnik/tablico brez interneta. Odpri HTML iz dejanske sinhronizirane mape v brskalniku oziroma upravljalniku datotek; to ni spletna stran gostovana na TrueNAS-u.
- **Spletne dejavnosti:** naloge, kvizi, forumi, strani in drugi moduli, katerih vsebina še ni prenesena, so jasno označeni **»Samo na e.FE«**. Imajo le povezavo na izvirni Moodle `view.php`, ki zahteva internet in prijavo; arhiv jih sam ne odpira in ne izvaja.
- **Iskanje:** iskanje predmetov na glavni strani in imen lokalnih datotek na strani predmeta poteka v brskalniku, brez zunanjih knjižnic ali omrežnih poizvedb.
- **Varnost:** HTML ne vključuje Moodle piškotkov, `sesskey`, zunanjih skript ali izvirnega HTML-ja s spletne učilnice. Imena modulov/datotek se HTML-escape-ajo, lokalni linki so URL-encoded. Generator ne prepiše obstoječih uporabnikovih datotek `index.html`, ki nimajo njegove oznake.

**Pomembno:** to je vizualna in navigacijska podobnost, ne uradna ali funkcionalna kopija Moodla. Zavihki za ocene, kompetence in dejavnosti so označeni kot nedostopni lokalno; nimajo lažne funkcionalnosti. Prikazani so le moduli, ki jih beta 1.2 že zazna iz povezav; ne prenaša vsebine forumov, rokov in drugih interaktivnih dejavnosti. Izvorna FE gradiva ostanejo nedotaknjena.


- Obnovi e.FE sejo s 0600 zaščiteno datoteko piškotkov, ki jo uporabnik izvozi z že preizkušenim lokalnim diagnostičnim orodjem v1.0.6.
- Uporabi samo Moodle dokumentirano **bralno** AJAX funkcijo `core_course_get_enrolled_courses_by_timeline_classification` (POST je samo za ta bralni klic); nato GET za strukturo predmetov, Moodle mape in `pluginfile.php` datoteke.
- Odkrije vse predmete, iz vidnih modulov prenese datoteke v Moodle *Mapah*. Različne podmape ostanejo ohranjene. Podprte so običajne študijske vrste datotek (PDF, Office, ZIP, slike, besedilo in izvorna koda).
- Shranjuje metapodatke o predmetih/dejavnostih v `predmet.json`, SHA-256 in HTTP ETag/Last-Modified v SQLite na ločenem `/state` volumnu. Ponovna sinhronizacija preverja spremembe; ne briše odstranjenih gradiv in nikoli ne prepiše lokalno spremenjenega gradiva.
- Poganja se vsaki 2 uri (nastavljivo), ohranja `/state/status.json`, ne izvaža gesla/sesskey/piškotkov v poročila.
- FE mount je privzeto **read-only**, le izbrane podmape `Spletna-ucilnica` imajo dodatne RW bind mounte. Ni odprtih portov.

## Ne podpira (še)

- Forumi, objave, napovedi, roki, koledar in vsebine Moodle *Strani/Nalog/Kvizov* se **ne prenašajo**. Naslovi dejavnosti so sicer shranjeni v `predmet.json` za naslednjo iteracijo.
- Moodle `resource` je od beta1.4 podprt, kadar vodi do datoteke iz `mod_resource/content` na uradni domeni e.FE. Ne podpira `book`, H5P, datotek, skritih v JavaScriptu, zahtevnejših predvajalnikov ali zunanjih URL-jev. Nekatere aktivnosti `resource` lahko ostanejo označene »e.FE«, če jih ni mogoče varno razrešiti.
- Če se SSO seja izteče ali je vezana na napravo/IP, bo sinhronizacija obstala; potrebna je ponovna ročna prijava in osvežitev datoteke piškotkov. V Dockerju **ni** shranjenega gesla.
- Študentska prijavna seja nima strežniško omejenih pravic branja; koda pa nima funkcij za oddajo, kviz ali spreminjanje učilnice. Moodle lahko beleži obisk strani.

## beta1.9.0 — prijava v e.FE z enim klikom iz FE mape

Nova storitev `fe-login` z zasebnim Chromiumom prek Selenium/noVNC omogoča obnovitev računa UL SSO na telefonu brez prenosnika. Ob prvem zagonu ustvari `Spletna-ucilnica/POVEZI.html`. Klik samodejno odpre zasebni portal, ki zažene Chromium, preveri Moodle sejo in sproži takojšnjo sinhronizacijo. Potreben je TrueNAS Tailscale IPv4 naslov v Compose datoteki (glej [navodila](docs/one-click-login.md)). Brskalnik nima neposredno objavljenih vrat in portal nima dostopa do Docker socketa. Zasnovano za enega uporabnika, ne za javno komercialno prijavo.


## Namestitev na TrueNAS SCALE

Predpogoj: FE Syncthing mapa je `/mnt/tank/sync/FE`; obstoječi FE Gradiva servis ostane nespremenjen. Ta servis ne namestiš v isto Compose aplikacijo kot FE Gradiva. Vsi koraki s skrivnostmi naj potekajo izključno lokalno, ne prek GitHuba ali ChatGPT.

### 1. Ustvari mape na TrueNAS

V **TrueNAS Shell**:

```sh
sudo install -d -m 750 \
  /mnt/tank/sync/FE/1-semester/01_MAT/Spletna-ucilnica \
  /mnt/tank/sync/FE/1-semester/02_FIZ_MiT/Spletna-ucilnica \
  /mnt/tank/sync/FE/1-semester/03_PROG/Spletna-ucilnica \
  /mnt/tank/sync/FE/1-semester/04_ELE/Spletna-ucilnica \
  /mnt/tank/sync/FE/Spletna-ucilnica \
  /mnt/tank/apps/fe-moodle-sync/state
sudo install -d -m 700 /mnt/tank/apps/fe-moodle-sync/secrets
```

Če so lokacije FE predmetov drugačne, prilagodi mount poti in `COURSE_DESTINATIONS` v `fe_sync/sync.py` ter znova zgradi sliko.

### 2. Varno kopiraj prijavne piškotke (NE v GitHub)

Na prenosniku je datoteka nastala po `fe_moodle_probe.py browser-login`:

```text
~/.local/share/fe-moodle-diagnostika/browser-profile/moodle-session-cookies.json
```

Prek SCP prenesi datoteko na svoj **SSH uporabniški račun TrueNAS** (zamenjaj `UPORABNIK`):

```sh
scp ~/.local/share/fe-moodle-diagnostika/browser-profile/moodle-session-cookies.json \
  UPORABNIK@192.168.1.120:~/moodle-session-cookies.json
```

Na TrueNAS Shell (v datoteki so občutljivi sejni piškotki):

```sh
sudo install -m 600 ~/moodle-session-cookies.json \
  /mnt/tank/apps/fe-moodle-sync/secrets/moodle-session-cookies.json
rm -f ~/moodle-session-cookies.json
```

Če je aplikacija že nameščena in osvežuješ sejo, posodobi vsebino iste datoteke brez menjave bind-mounta oziroma znova zaženi aplikacijo. **Nikoli ne pošiljaj datoteke piškotkov v chat ali Syncthing.** TrueNAS uporabnik, ki lahko bere te piškotke, ima dostop kot tvoj Moodle račun.

### 3. Nadgradi obstoječo TrueNAS aplikacijo

Če imaš že nameščeno in delujočo različico, v **Apps → fe-moodle-sync → Edit** spremeni **samo** sliko na `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.4`, shrani ter počakaj na naslednjo sinhronizacijo. Ne briši `/state/files.sqlite` ali obstoječih PDF-jev. Pravice/mounti ostajajo enaki.

Za takojšnjo generacijo HTML strani brez dodatnega čakanja lahko po zagonu vsebnika uporabiš:

```sh
sudo docker exec ix-fe-moodle-sync-fe-moodle-sync-1 python -m fe_sync once
```

Pozor: enkratnega ročnega zagona ne izvajaj vzporedno z že tekočo samodejno sinhronizacijo. Če želiš preprečiti dvojno izvajanje, raje samo znova zaženi aplikacijo in počakaj, da se konča prvi pregled. Če seja poteče, bo treba obnoviti prijavo kot pri beta 1.0.

### 4. Namesti Docker Compose (novi uporabniki)

V **TrueNAS → Apps → Discover Apps → Custom App → Install via YAML** uporabi [deploy/truenas-compose.yaml](deploy/truenas-compose.yaml). Ime npr. `fe-moodle-sync`.

Slika: `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.4`. GHCR paket mora biti javno viden ali mora TrueNAS imeti prijavo v GHCR. **Ne nameščaj**, dokler GitHub Actions ni uspešno objavil slike.

Privzeta ura sinhronizacije: vsaki 2 uri od zagona. Za prvi preizkus poišči dnevnike:

```sh
sudo docker ps --filter name=fe-moodle
sudo docker logs --tail 100 <ID_VSEBNIKA>
sudo cat /mnt/tank/apps/fe-moodle-sync/state/status.json
```

Preveri `status` (`ok`/`partial`/`failed`) in `files.downloaded`. Če vidiš `failed` in `MoodleError`, je lahko seja potekla ali ni prenosljiva na NAS.

### 5. Kaj nastane v FE mapah

```text
FE/
  1-semester/
    01_MAT/
      Spletna-ucilnica/
        index.html   # lokalni pogled predmeta
        predmet.json
        Gradiva/
          Predavanja__144497/
            M1vs-2026-uvod.pdf
          Izpiti__144500/
            2026/izp_mat1vs_2026_1.pdf
    04_ELE/Spletna-ucilnica/...
  Spletna-ucilnica/
    index.html       # glavna stran s predmeti
    Ostali-predmeti/
      4161_Programiranje_II/
        index.html
```

Ker je to ista FE Syncthing mapa, bo FE Gradiva dokumente zaznal ob naslednjem indeksiranju. Če ne želiš kopiranja vseh prilog na tablico, v Syncthing dodaj ustrezne ignore vzorce (ne izbriši datotek na NAS-u).

## Lokalni razvoj in testiranje

```sh
python -m pip install -r requirements.txt pytest
python -m pytest -q
python -m fe_sync once   # zahteva FE_ROOT, SYNC_STATE_DIR in FE_MOODLE_SESSION_FILE
```

GitHub Actions preveri teste in Docker build ter ob pushu na `main` objavi GHCR `beta1.1` in `latest`. Za nadaljnje izdaje uporabimo nove oznake, ne tiho spreminjanje produkcijske slike.

## Varnost

Zunanje povezave s sejnimi podatki se zavračajo, prenosi so omejeni na e.FE in dovoljene `/pluginfile.php/.../mod_folder/content/...` poti. `POST` se uporablja samo za eno bralno Moodle funkcijo. PDF ima preverjen `%PDF-` podpis in obstaja velikostna omejitev. Koda ne izbriše starih datotek; pred spremembo preveri njihov SHA-256 in ne prepiše ročno spremenjenih kopij. Prijavna datoteka je bind-mountana samo za branje, FE gradiva se zapisujejo samo v namenske mape.

**Omejitev:** na pravi strani in z živo sejo izvajanje še ni neposredno preverjeno; pred obsežnim prenosom preglej dnevnike, dovolj prostora na NAS-u in lokalne pravice map.


## Kaj je novega v beta 1.2

- FE-podobna navigacija (bela vrhnja vrstica, rdeč meni), tanke vrstice dejavnosti, rdeči poudarki.
- Moodle razdelki, kjer jih HTML strani poda, z možnostjo skrivanja in razpiranja; rezervna razvrstitev le za potrjene module Matematike I.
- Mape so privzeto zaprte; klik pokaže prenesene datoteke in podmape z relativnimi lokalnimi povezavami.
- Iskanje po imenih gradiv in filter `Vsa vsebina` / `Samo lokalno` / `Samo e.FE`.
- Rezultat je še vedno en statičen HTML na predmet, brez omrežnih skript ali dostopa do sejnih piškotkov.

### Nadgradnja beta 1.1 → beta 1.2

V TrueNAS Custom App → **Edit** zamenjaj le `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.1` z `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.2.1`.
Shrani spremembo, počakaj na prvi ciklus oziroma ga sproži z
`sudo docker exec ix-fe-moodle-sync-fe-moodle-sync-1 python -m fe_sync once`
(po potrebi preveri, da ne teče drug cikel hkrati). Ni treba brisati baze ali ponovno prenašati že shranjenih PDF-jev.

HTML se posodobi po uspešni sinhronizaciji. Ob iztečeni seji ostane zadnja lokalna kopija; obnoviti je treba sejo, preden se osveži.


## Ponovno ustvarjanje lokalnega HTML brez prijave (beta1.2.1)

Če se Moodle prijava izteče ali je bilo ustvarjanje HTML neuspešno, lahko
preveriš in ponovno ustvariš HTML samo iz že shranjenih `predmet.json` in
`/state/files.sqlite`. Povezava z e.FE pri tem ni potrebna:

```sh
sudo docker exec ix-fe-moodle-sync-fe-moodle-sync-1 python -m fe_sync render
```

Novi `render` način ne prenese ali spremeni datotek gradiv. Posodobi le HTML
strani z znanim generiranim označevalcem. Če naleti na uporabniški `index.html`
brez označevalca, ga ne prepiše in izpiše razlog, vključno z ID-jem predmeta.

V beta1.2.1 so popravljeni tudi naslovi Moodle razdelkov brez besedila
»Izberi poglavje / Skrči / Razširi«.

**Odpravljena napaka v beta1.1/1.2:** Označevalec našega HTML-ja je za
velikim vgrajenim CSS, zato preverjanje prvih 1.800 znakov ni delovalo ob
vsaki naslednji sinhronizaciji. Beta1.2.1 preveri celotno obstoječo datoteko
pred varno atomsko zamenjavo; tujih HTML datotek še vedno ne prepisuje.

## beta1.3: zadnja osvežitev in enostavno ročno podaljšanje UL SSO

Po vsakem ciklu (tudi neuspešnem) se posodobita datoteki:

- `FE/Spletna-ucilnica/STANJE.html` — odpri v lokalni e.FE, prikazuje
  **zadnjo uspešno sinhronizacijo**, zadnji poskus in status napak.
- `FE/Spletna-ucilnica/STANJE.txt` — enostaven povzetek za upravljalnik datotek.

Na glavni strani in na straneh predmetov je povezava »Zadnja osvežitev«.
Če se seja izteče, je zadnja uspešna osvežitev ohranjena; ne prikazujemo
neuspešnega poskusa kot uspeh. Piškotki in gesla nikoli niso v FE.

### Obnovitev UL SSO z enim ukazom (Arch Linux prenosnik)

Na prenosniku mora biti še vedno `~/Downloads/fe_moodle_probe.py` v1.0.6
in obstoječa `~/Downloads/.venv` z nameščenim Playwrightom. Enkrat shrani
`scripts/obnovi-sejo.sh` v `~/Downloads/obnovi-sejo.sh` ter zaženi:

```bash
bash ~/Downloads/obnovi-sejo.sh
```

Skripta odpre Chromium. Prek UL SSO se prijavi **ročno** in pritisni ENTER.
Nato skripta preveri bralni Moodle API, prek `scp -p` varno prenese piškotke
na TrueNAS, skozi `ssh` in `sudo` **prepiše vsebino obstoječe datoteke**
`/mnt/tank/apps/fe-moodle-sync/secrets/moodle-session-cookies.json`,
spet zaščiti pravice 0600, odstrani vmesno kopijo in znova zažene
obstoječi Docker vsebnik. Po ponovnem zagonu preveri 11 predmetov, če je
seja uspešna. Skripta ne shrani gesla, ničesar ne oddaja na Moodle in ne
izvozi sejnih piškotkov v Syncthing.

Omejitev: SSO zaradi varnosti še vedno zahteva tvoje ročno potrjevanje;
prijave ni mogoče podaljševati samodejno brez dodatnega (nepriporočljivega)
shranjevanja prijavnih skrivnosti. Na računalniku potrebuješ delujoč SSH
povezavo do `admin@192.168.1.120` in TrueNAS sudo pravice. Če imaš drugo
lokacijo, nastavi `FE_PROBE_PATH`, `FE_PYTHON`, `FE_NAS_HOST` ali
`FE_NAS_USER` pred zagonom.

Po nadgradnji Docker slike na `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.3`
počakaj na prvi cikel ali ročno poženi `python -m fe_sync render`, ki
osveži prikaz stanja iz shranjenega zadnjega `status.json` brez Moodle zahtev.


## beta1.4 — samostojne datoteke e.FE

Novi **bralni** način prenese vsebino samostojnih Moodle dejavnosti `Datoteka`
(`resource`). Pri dostopu `GET /mod/resource/view.php?id=...` sledi
**le preverjenim** povezavam `/pluginfile.php/.../mod_resource/content/...`
znotraj `https://ucilnica-fe.uni-lj.si`. Razume varno neposredno preusmeritev
ali povezavo do vgrajene datoteke v HTML. Nikoli ne sledi zunanji domeni ali
prenaša poljubne povezave iz vsebine. Prijava ostane lokalna na TrueNAS-u.

Nastane npr.:

```text
FE/1-semester/03_PROG/Spletna-ucilnica/
  Gradiva/Samostojne-datoteke/
    E-učbenik__144657/E-učbenik.pdf
  index.html
```

Vsaka datotečna dejavnost ima lastno mapo z Moodle ID-jem. Imena so varna za
Syncthing/Android. HTML namesto povezave »e.FE« prikaže lokalni PDF,
če je uspešno prenesen. Obstoječi PDF-ji se ne prepišejo, če so bili
ročno spremenjeni, posnetki in neinteraktivni podatki se ne brišejo.

Za prvi preizkus:
1. Posodobi **samo** sliko na
   `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.4` (ne `latest`).
2. Po končanem ciklu preberi `/mnt/tank/apps/fe-moodle-sync/state/status.json`.
   `resources_found` pomeni število zaznanih, `resources_processed`
   uspešno obdelane datoteke, `resources_unsupported` vsebine brez
   potrjene datotečne povezave, `resources_errors` napake.
3. Preveri novi HTML in Syncthing na tablici.

**Pomembno:** `render` samo ponovno ustvari HTML in **ne prenaša** novih
datotek. Za `resource` prvič počakaj na sinhronizacijski cikel.
Možnost `once` ni primerna za vzporedni zagon s samodejnim ciklom.
Gradnja v CI ne potrjuje dostopa do zasebnih gradiv e.FE; namen tega koraka
je preverjanje na resnični namestitvi brez tveganja za spremembe v e.FE.


## beta1.4.1 — odpornost na kratke prekinitve povezave

Na prvi beta1.4 sinhronizaciji je bilo 19. 10. 2026 odkritih 132 Moodle
resource dejavnosti, 130 obdelanih, 2 nepodprti. Prenesenih je bilo 103 novih
datotek (397.550.678 bajtov); GET zadnjega predmeta je prekinil RemoteDisconnected.

Popravek:
- Idempotentne GET zahteve ponovi do 3-krat ob ConnectionError/Timeout
  (premora 1 s in 2 s). POST, prijavnih/SSO napak in redirekcij ne ponavlja.
- Če omrežje za posamezni predmet odpove tudi po ponovitvah, uporabi samo
  prejšnji predmet.json za *lokalni prikaz* predmeta; označi ga kot `cached`,
  ne kot novo pregledanega. Za predmete brez starega manifesta ničesar ne izmišlja.
- Če odpove seznam datotek Moodle mape, nadaljuje z ostalimi mapami/predmeti.
- Tudi ob delno uspešnem pregledu osveži lokalne HTML povezave na varno
  shranjene datoteke in ohrani datum zadnje popolnoma uspešne sinhronizacije.
- V status.json: `courses_unavailable`, `courses_from_cache`,
  `folders_unavailable` ter `status=partial` za nedosegljivost.

Namesti le `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.4.1`.
Ne briši `files.sqlite`, PDF-jev ali prijavnih piškotkov. Po prvem
avtomatskem ciklu preveri `status.json` in lokalni HTML.


## beta1.5 — lokalne Moodle strani in obvestila (omejen bralni arhiv)

- Zajem do 19 Moodle dejavnosti tipa Stran, samo besedila iz izrecnih
  Moodle vsebinskih blokov (npr. \`no-overflow\`). Neprepoznani prikazi se
  ne shranijo; izvirna e.FE povezava ostane.
- Arhiv forumov, katerih naslov vsebuje Obvestila / Forum novic /
  Announcements / News: največ 5 prvih razprav **v vrstnem redu, kot jih
  prikaže Moodle**, ne nujno kronološko najnovejših.
- Besedilo prve prepoznane objave v razpravi se shrani kot varen lokalni HTML
  in Markdown, brez izvirnega HTML-ja, skript, obrazcev, piškotkov ali sesskey.
- Priloge prenese samo iz varnih poti \`mod_forum/attachment\` na uradni e.FE
  (največ 12 na razpravo), s preverjanjem PDF-podpisa, dovoljenih končnic,
  velikosti, SHA-256 in zaščito pred spremembami.
- Na posamezni strani predmeta lahko odpreš lokalno kopijo pod
  \`Gradiva/Moodle-strani/<ime>__<id>/index.html\` oziroma
  \`Gradiva/Obvestila/<ime>__<id>/index.html\`. Poleg HTML nastane tudi
  datoteka \`.md\`, ki jo lahko indeksira FE Gradiva.
- Oddelek ostane povezava na e.FE, če parser ne prepozna vsebine;
  arhiv nima samodejnega oddajanja nalog ali spreminjanja forumov.
- Status \`status.json\` vključuje \`pages_found\`, \`pages_archived\`,
  \`pages_unsupported\`, \`pages_errors\`, \`announcements_found\`,
  \`announcement_posts_archived\`, \`announcement_attachments_processed\`.
- Pred namestitvijo na TrueNAS je treba preveriti GitHub Actions ter nato
  na pravi e.FE seji preveriti, katere Moodle različice strani/forumi
  dejansko ustrezajo parserju. Testi uporabljajo sintetične HTML primere.
- Nadgradi le sliko na \`ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.5\`;
  ohrani volumne in SQLite. \`render\` ne zajema novih vsebin.


## beta1.5.1 — forum, ki preusmeri na pregled predmeta

Primer: MakerLab, Moodle modul 123693 vrne HTTP 303, medtem ko
uporabnikova prijavljena stran in predmet odgovarjata HTTP 200.

Če `/mod/forum/view.php?id=N` preusmeri na **isti uradni e.FE
`/course/view.php?id=C`**, in je `C` natanko ID obdelovanega
predmeta brez dodatnih parametrov, to obravnavamo kot nepodprto oziroma
neprebrano forumsko dejavnost. Brez sledenja preusmeritvi, brez
pretvarjanja, da je bila vsebina prenesena. V statusu sta
`announcements_unsupported` in `announcements_returned_to_course`,
STANJE.html/txt pa prikažeta opozorilo.

Preusmeritev na prijavo, drugi predmet, zunanjo domeno ali nepričakovane
parametre še vedno pomeni napako. Ni oslabljene varnostne omejitve.
Za nadgradnjo uporabi `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.5.1`.
Ne briši baze, sejnih piškotkov, PDF-jev ali zapiskov.


## beta1.6 — novosti in koledar (19. oktober 2026)

**Novosti:** Po vsaki sinhronizaciji primerja dejansko prenesene datoteke in
upravljane besedilne zapiske (Moodle Stran/Obvestila) s stanjem ob začetku
cikla. Novo ali spremenjeno gradivo zapiše v SQLite zgodovino, omejeno na
zadnjih 30 dni in 300 zapisov. Novosti niso isto kot vse spremembe na
Moodle: spremembe nedostopnih kvizov, oddaj in strani brez podprtega
besedila ne morejo biti odkrite. Osebne zapiske zunaj upravljanih
Spletna-ucilnica map ignorira in jih nikoli ne prepisuje.
V lokalni učilnici odpri `Spletna-ucilnica/NOVOSTI.html` ali besedilni
`NOVOSTI.txt`. Zgodovina se ne izbriše po vsaki naslednji sinhronizaciji.

**Koledar:** Uporablja izključno e.FE-jev **bralni** Moodle AJAX klic
`core_calendar_get_action_events_by_timesort`. Zahteva le dogodke zadnjih
7 in prihodnjih 183 dni; prikaže samo dogodke, katerih course ID ustreza
vpisanemu predmetu in imajo preverljiv celoštevilski UNIX čas. Za
`rok` označi samo jasno označene Moodle akcijske dogodke
`due/submission/submissiondue` za `assign/quiz`, vse druge dogodke
kot `dogodek`. Ure na lokalni strani so označene **UTC**; prikazan čas
ni vedno rok oddaje. Moodle API lahko vrne le akcijske dogodke, torej
to **ni popoln urnik vseh kolokvijev**. Za pravo veljavnost in oddajo
naloge vedno preveri izvirno e.FE.

Če je API podprt, `Spletna-ucilnica/KOLEDAR.html` vsebuje dogodke in
`Spletna-ucilnica/KOLEDAR.ics` standarden iCalendar za **ročen uvoz**.
Uvoz .ics ni naročnina in se ne posodablja samodejno v aplikaciji
Koledar. Če Moodle zavrne API, lokalna stran pošteno napiše, da
koledarski podatki niso na voljo; ni izmišljenih datumov.
Morebitni prejšnji ICS se takrat ne briše. Če je odziv omejen
(več kot 200 dogodkov), se to jasno označi.
Koledarja ali drugih podatkov ne pošiljamo tretjim strežnikom.

Namestitev: zamenjaj samo Docker oznako na
`ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.6`.
Obstoječe SQLite baze, piškotkov, PDF-jev in Syncthing map NE briši.
Po zaključku poglej `status.json`: nova polja `changes` ter
`calendar` (z `available`, `events`, `truncated`). Ob nedosegljivem
koledarju lahko osnovna sinhronizacija vseeno uspe, a status to
posebej navede kot `calendar.available=false`.


## beta1.7 — globalno iskanje, priljubljeni predmeti in slovenski koledar

**ISKANJE.html** je nova globalna iskalna stran. Po vseh vpisanih
predmetih išče imena prenešenih datotek, gradiv in dejavnosti ter do 2.400
znakov iz besedil varno arhiviranih Moodle strani in objav. Vrste rezultatov:
predmeti, PDF, dokumenti, slike, koda, arhivi, Moodle strani/obvestila,
druge datoteke in dejavnosti, ki so na voljo samo na e.FE.
Filtri omogočajo tudi izbiro posameznega predmeta. Na lokalno shranjene
datoteke se poveže neposredno; spletne dejavnosti vodijo na e.FE.
Celoten indeks rezultatov je znotraj ene generirane HTML datoteke in
deluje tudi prek Androidovega \`file://\` dostopa **brez zunanjega JS,
fetch() ali spletnega strežnika**. Ne izvaja iskanja znotraj PDF,
Word/PowerPoint ali osebnih zapiskov; druge zasebne FE datoteke ne
postanejo del indeksa. Omejitev je 5.000 indeksiranih zadetkov.

**Priljubljeni predmeti**: na domači strani klikni zvezdico na kartici
predmeta. Priljubljeni so prikazani prvi. Nastavitev se shrani izključno
v \`localStorage\` trenutnega brskalnika, kadar ta to dovoljuje.
Pri nekaterih Android brskalnikih ali pregledovalnikih \`file://\`
shranjevanje ne deluje; takrat zvezdica velja samo do zaprtja strani.
**Priljubljenih Syncthing ne sinhronizira med napravami**, ker ostajajo
lokalna nastavitev brskalnika.

**KOLEDAR.html**: dogodke iz preverjenih UNIX datumov pretvori v
slovenski čas prek \`Europe/Ljubljana\` in upošteva poletni/zimski čas
(CEST = UTC+2, CET = UTC+1). Dogodki so združeni po dnevih; gumbi
ločijo jasno označene roke oddaje od drugih dogodkov. Ničesar ne
sklepa iz naslovov ali opisov kolokvijev. \`.ics\` še naprej vsebuje
pravilne UTC datume; ob uvozu jih koledarska aplikacija pretvori sama.
Odvisnost \`tzdata\` zagotovi časovni pas tudi v minimalnem Dockerju.

**Varnost:** Program ostaja enosmeren in samo bere e.FE. Ne spreminja
izvirnih profesorjevih gradiv, oddaj ali kvizov, ne shranjuje piškotkov
v FE mapo in ne briše ročnih zapiskov. Obstoječi \`status.json\`, SQLite
in PDF-ji ostanejo ohranjeni.

**Nadgradnja TrueNAS:** zamenjaj samo sliko na
\`ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.7\`. Nato preveri
\`navigation.search.items\` v \`status.json\` in odpri
\`Spletna-ucilnica/ISKANJE.html\` ter \`KOLEDAR.html\`.
Zgodovina sprememb, število HTML strani in spletne povezave
so še vedno pravilno označeni.


## beta1.8 — iskanje po besedilu PDF-jev

- Znotraj \`Spletna-ucilnica/ISKANJE.html\` je mogoče iskati po **besedah iz
  besedilne plasti** PDF-jev, ki jih je program zanesljivo prenesel iz e.FE.
  Primer: \`Coulombov zakon\` najde dokument, ki vsebuje **obe besedi**,
  lahko na različnih mestih. To **ni iskanje natančnih fraz** ali
  lematizacija; velike/male črke in slovenski šumniki so normalizirani.
  Že obstoječe iskanje imen, predmetov, Moodle strani in dejavnosti ostaja.
- Ob prvem zagonu se začne predobdelava obstoječih PDF-jev, brez spreminjanja
  datotek in brez novega prenosa z e.FE. Besedne zbirke (ne izvorna besedila)
  so shranjene **stisnjeno v SQLite** \`/state/files.sqlite\` pod
  \`pdf_search_index\`. Napredovanje poteka postopoma, največ 400 na cikel,
  največ približno 210 sekund dela na sinhronizacijo. Ponovne
  sinhronizacije uporabljajo predpomnilnik, če se SHA izvirne datoteke in
  lokalni podatki datoteke niso spremenili.
- Branje posameznega PDF-ja poteka v izoliranem pomožnem procesu, z omejitvijo
  25 sekund in 384 MiB naslovnega prostora. Dokumenti nad 50 MiB,
  šifrirani/poškodovani PDF-ji in dokumenti brez besedilne plasti so
  označeni kot nepodprti oziroma neiskalni. Zelo dolgi PDF-ji so
  indeksirani **delno**, največ prvih 350 strani, največ 18.000 unikatnih
  besed in 1.500.000 besedilnih znakov. Ni OCR, zato skenirane slike v PDF-ju
  niso preiskljive.
- V \`status.json\` se pojavi polje \`pdf_index\` s številom
  \`total\`, \`searchable\`, \`ready\`, \`partial\`, \`no_text\`,
  \`unsupported\`, \`bad_pdf\`, \`timeout\`, \`modified\`,
  \`pending\`, \`processed\`, \`cached\`. \`navigation.search.pdf_word_entries\`
  prikazuje število dejansko v HTML indeksiranih PDF-jev.
- Iskalni indeks je **vdelan v lokalno HTML stran**: ne zahteva interneta,
  datotečnega strežnika, JavaScript \`fetch\` ali oblačnega API-ja.
  PDF-ji se ne nalagajo ali pošiljajo tretjim ponudnikom. Uporabnikovi
  ročni zapiski in nenadzorovane datoteke niso vključeni.
- Če se PDF lokalno spremeni in se ne ujema s prvotno preneseno SHA,
  njegova stara iskalna vsebina ni prikazana, njegovih sprememb pa
  program ne prepisuje. Datotek in obstoječih SQLite podatkov ni treba brisati.
- Nadgradnja TrueNAS: uporabi
  \`ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.8\`. Po prvem ciklu
  preveri \`pdf_index.pending\`: če je večji od 0, bodo preostali PDF-ji
  obdelani v naslednjih ciklih (privzeto vsakih 2 h). \`render\` samo
  prebere že pripravljen PDF predpomnilnik, ne prične novega indeksiranja.


## beta1.8.1 — tišji dnevnik pypdf

Nekateri PDF-ji vsebujejo nepravilne navzkrižne reference (xref), ki
jih pypdf s \`strict=False\` zna samodejno prezreti in popraviti.
Primer ponavljajočega se opozorila:
\`Ignoring wrong pointing object 24 0 (offset 0)\`.

Zdaj **samo izolirani proces bralnika PDF-ja** utiša natanko opozorila
\`pypdf._reader\`, ki se začnejo z
\`Ignoring wrong pointing object \`. Vsa **druga** pypdf opozorila in
napake ostanejo vidna. Stanja \`pdf_index.bad_pdf\`,
\`pdf_index.timeout\`, \`pdf_index.partial\`, \`pdf_index.no_text\`
in \`pdf_index.pending\` se še naprej pošteno zbirajo v
\`/state/status.json\` in statusni strani.

Sprememba ne spreminja PDF-jev, SQLite predpomnilnika, pravic,
sejnih piškotkov ali vsebine iskalnega indeksa. Če beta1.8 zaključi
sinhronizacijo s statusom \`ok\` in poljem \`pdf_index\`, lahko ostane
nameščena; beta1.8.1 je neobvezna izboljšava diagnostike.
