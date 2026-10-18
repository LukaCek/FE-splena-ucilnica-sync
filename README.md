# FE Moodle Sync — beta 1.0

Enosmerno, samodejno arhiviranje **datotek iz Moodle map** [e.FE](https://ucilnica-fe.uni-lj.si) na osebni TrueNAS, kjer jih obstoječi FE Gradiva MCP lahko bere. Ločen servis, brez lokalnega AI ali Chromiuma v Dockerju.

**Stanje:** beta 1.0, kodo in teste je treba potrditi tudi proti dejanski prijavljeni seji na TrueNAS-u; dotlej ni potrjen produkcijski prenos.

## Kaj zmore

- Obnovi e.FE sejo s 0600 zaščiteno datoteko piškotkov, ki jo uporabnik izvozi z že preizkušenim lokalnim diagnostičnim orodjem v1.0.6.
- Uporabi samo Moodle dokumentirano **bralno** AJAX funkcijo `core_course_get_enrolled_courses_by_timeline_classification` (POST je samo za ta bralni klic); nato GET za strukturo predmetov, Moodle mape in `pluginfile.php` datoteke.
- Odkrije vse predmete, iz vidnih modulov prenese datoteke v Moodle *Mapah*. Različne podmape ostanejo ohranjene. Podprte so običajne študijske vrste datotek (PDF, Office, ZIP, slike, besedilo in izvorna koda).
- Shranjuje metapodatke o predmetih/dejavnostih v `predmet.json`, SHA-256 in HTTP ETag/Last-Modified v SQLite na ločenem `/state` volumnu. Ponovna sinhronizacija preverja spremembe; ne briše odstranjenih gradiv in nikoli ne prepiše lokalno spremenjenega gradiva.
- Poganja se vsaki 2 uri (nastavljivo), ohranja `/state/status.json`, ne izvaža gesla/sesskey/piškotkov v poročila.
- FE mount je privzeto **read-only**, le izbrane podmape `Spletna-ucilnica` imajo dodatne RW bind mounte. Ni odprtih portov.

## Ne podpira (še)

- Forumi, objave, napovedi, roki, koledar in vsebine Moodle *Strani/Nalog/Kvizov* se **ne prenašajo**. Naslovi dejavnosti so sicer shranjeni v `predmet.json` za naslednjo iteracijo.
- Moodle `resource` datoteke zunaj modulov *Mapa*, `book`, H5P, zahtevani AJAX prikaz, eksterni URL-ji in gradiva dostopna zgolj prek JavaScripta niso del beta 1.0.
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

### 3. Namesti Docker Compose

V **TrueNAS → Apps → Discover Apps → Custom App → Install via YAML** uporabi [deploy/truenas-compose.yaml](deploy/truenas-compose.yaml). Ime npr. `fe-moodle-sync`.

Slika: `ghcr.io/lukacek/fe-splena-ucilnica-sync:beta1.0`. GHCR paket mora biti javno viden ali mora TrueNAS imeti prijavo v GHCR. **Ne nameščaj**, dokler GitHub Actions ni uspešno objavil slike.

Privzeta ura sinhronizacije: vsaki 2 uri od zagona. Za prvi preizkus poišči dnevnike:

```sh
sudo docker ps --filter name=fe-moodle
sudo docker logs --tail 100 <ID_VSEBNIKA>
sudo cat /mnt/tank/apps/fe-moodle-sync/state/status.json
```

Preveri `status` (`ok`/`partial`/`failed`) in `files.downloaded`. Če vidiš `failed` in `MoodleError`, je lahko seja potekla ali ni prenosljiva na NAS.

### 4. Kaj nastane v FE mapah

```text
FE/
  1-semester/
    01_MAT/
      Spletna-ucilnica/
        predmet.json
        Gradiva/
          Predavanja__144497/
            M1vs-2026-uvod.pdf
          Izpiti__144500/
            2026/izp_mat1vs_2026_1.pdf
    04_ELE/Spletna-ucilnica/...
  Spletna-ucilnica/Ostali-predmeti/
    4161_Programiranje_II/...
```

Ker je to ista FE Syncthing mapa, bo FE Gradiva dokumente zaznal ob naslednjem indeksiranju. Če ne želiš kopiranja vseh prilog na tablico, v Syncthing dodaj ustrezne ignore vzorce (ne izbriši datotek na NAS-u).

## Lokalni razvoj in testiranje

```sh
python -m pip install -r requirements.txt pytest
python -m pytest -q
python -m fe_sync once   # zahteva FE_ROOT, SYNC_STATE_DIR in FE_MOODLE_SESSION_FILE
```

GitHub Actions preveri teste in Docker build ter ob pushu na `main` objavi GHCR `beta1.0` in `latest`. Za nadaljnje izdaje uporabimo nove oznake, ne tiho spreminjanje produkcijske slike.

## Varnost

Zunanje povezave s sejnimi podatki se zavračajo, prenosi so omejeni na e.FE in dovoljene `/pluginfile.php/.../mod_folder/content/...` poti. `POST` se uporablja samo za eno bralno Moodle funkcijo. PDF ima preverjen `%PDF-` podpis in obstaja velikostna omejitev. Koda ne izbriše starih datotek; pred spremembo preveri njihov SHA-256 in ne prepiše ročno spremenjenih kopij. Prijavna datoteka je bind-mountana samo za branje, FE gradiva se zapisujejo samo v namenske mape.

**Omejitev:** na pravi strani in z živo sejo izvajanje še ni neposredno preverjeno; pred obsežnim prenosom preglej dnevnike, dovolj prostora na NAS-u in lokalne pravice map.
