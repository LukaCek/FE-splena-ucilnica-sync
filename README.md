# FE Moodle Sync — beta 1.2

Enosmerno, samodejno arhiviranje **datotek iz Moodle map** [e.FE](https://ucilnica-fe.uni-lj.si) na osebni TrueNAS, kjer jih obstoječi FE Gradiva MCP lahko bere. Ločen servis, brez lokalnega AI ali Chromiuma v Dockerju.

**Stanje:** beta 1.2. Beta 1.0 je 19. 10. 2026 uspešno prenesla 222 datotek iz 11 predmetov, beta 1.1 pa dodala osnovni HTML. Beta 1.2 spremeni predvsem prikaz in obdelavo razdelkov. Dejanski prikaz po nadgradnji na TrueNAS-u je treba še preveriti.

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
- Moodle `resource` datoteke zunaj modulov *Mapa*, `book`, H5P, zahtevani AJAX prikaz, eksterni URL-ji in gradiva dostopna zgolj prek JavaScripta niso del beta 1.2.
- Če se SSO seja izteče ali je vezana na napravo/IP, bo sinhronizacija obstala; potrebna je ponovna ročna prijava in osvežitev datoteke piškotkov. V Dockerju **ni** shranjenega gesla.
- Študentska prijavna seja nima strežniško omejenih pravic branja; koda pa nima funkcij za oddajo, kviz ali spreminjanje učilnice. Moodle lahko beleži obisk strani.

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

Če imaš že nameščeno in delujočo beta 1.0, v **Apps → fe-moodle-sync → Edit** spremeni **samo** sliko `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.0` na `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.1`, shrani ter počakaj na naslednjo sinhronizacijo. Ne briši `/state/files.sqlite` ali obstoječih PDF-jev. Pravice/mounti ostajajo enaki.

Za takojšnjo generacijo HTML strani brez dodatnega čakanja lahko po zagonu vsebnika uporabiš:

```sh
sudo docker exec ix-fe-moodle-sync-fe-moodle-sync-1 python -m fe_sync once
```

Pozor: enkratnega ročnega zagona ne izvajaj vzporedno z že tekočo samodejno sinhronizacijo. Če želiš preprečiti dvojno izvajanje, raje samo znova zaženi aplikacijo in počakaj, da se konča prvi pregled. Če seja poteče, bo treba obnoviti prijavo kot pri beta 1.0.

### 4. Namesti Docker Compose (novi uporabniki)

V **TrueNAS → Apps → Discover Apps → Custom App → Install via YAML** uporabi [deploy/truenas-compose.yaml](deploy/truenas-compose.yaml). Ime npr. `fe-moodle-sync`.

Slika: `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.1`. GHCR paket mora biti javno viden ali mora TrueNAS imeti prijavo v GHCR. **Ne nameščaj**, dokler GitHub Actions ni uspešno objavil slike.

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

## beta1.2.1: popravek HTML in offline ponovna izdelava

Prej je program iskal lastno oznako HTML samo v prvih 1800 znakih,
oznaka pa je bila za obsežnim CSS. Zato ni mogel posodobiti lastnih strani.
Sedaj preveri celotno datoteko in še vedno ne prepiše uporabnikovega HTML.

Brez Moodlove seje lahko HTML takoj obnoviš iz predhodno shranjenih
predmet.json in SQLite z ukazom:

    sudo docker exec ix-fe-moodle-sync-fe-moodle-sync-1 python -m fe_sync render

Naslovi razdelkov ne vsebujejo več Moodlovih gumbov Izberi poglavje/Skrči/Razširi.
