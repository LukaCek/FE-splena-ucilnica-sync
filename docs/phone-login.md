# Začasna prijava v e.FE s telefona

Ta prototip je namenjen enemu zaupanja vrednemu uporabniku in zasebnemu TrueNAS-u.
Ne služi kot javna večuporabniška prijavna storitev.

## Predpogoji

- TrueNAS Docker in obstoječi FE Moodle Sync iz tega repozitorija.
- Tailscale teče **na gostitelju TrueNAS** in na telefonu v istem tailnetu.
- Če sta telefon in NAS na istem Wi-Fi, je možen tudi zasebni LAN naslov NAS-a.
- Nobenega posredovanja vrat (port forwarding) ali javnega Cloudflare Tunnel za noVNC.

## Zaženi na TrueNAS Shell

```bash
curl -fsSLo /tmp/nas_phone_login.py \
  https://raw.githubusercontent.com/LukaCek/FE-splena-ucilnica-sync/main/scripts/nas_phone_login.py
sudo python3 /tmp/nas_phone_login.py start
```

Če Tailscale na gostitelju TrueNAS ni na voljo in si **v domačem omrežju**, uporabi
`sudo python3 /tmp/nas_phone_login.py start --bind-ip 192.168.1.120`
le, če je to trenutni zasebni naslov NAS-a. Za oddaljen dostop iz mobilnega omrežja je potrebna zasebna povezava.

Skript izpiše naslov brskalnika (vrata 17900) in naključno VNC geslo.
Odpri URL v brskalniku telefona, v prikazanem Chromiumu opravi UL SSO prijavo
in počakaj na seznam predmetov Moodle.

## Zaključi

```bash
sudo python3 /tmp/nas_phone_login.py finish
```

Skript preveri sejo samo na uradni domeni e.FE, varno shrani potrebne piškotke
v že obstoječo datoteko brez spremembe inode, ponovno zažene Moodle Sync
in potrdi dostop do predmetov. Po uspehu odstrani začasni brskalnik.

Če postopek prekineš, odstrani browser:

```bash
sudo python3 /tmp/nas_phone_login.py stop
```

## Varnost in produktni načrt

- Ne objavljaj VNC gesla ali vsebine prijavnih piškotkov in ne deli slik zaslona med vnosom gesla.
- Selenium WebDriver se veže izključno na 127.0.0.1, noVNC pa na zasebni Tailscale/LAN naslov.
- Piškotki so občutljiva uporabniška skrivnost in jih ne smeš shranjevati v GitHub, Syncthing ali javno mapo FE.
- To je začasen osebni prototip. Za komercialno aplikacijo najprej preveri dostopnost in pogoje uradnega Moodle API/tokena ali odobrene integracije z UL; običajna Moodle OAuth2 prijava ne pomeni, da je Moodle sam OAuth2 ponudnik.
- Obdelava osebnih podatkov in dostop do študijskih gradiv zahtevata primerno pravno podlago in dogovor z upravljavcem.
