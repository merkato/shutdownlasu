# Przygotowanie podstawowych elementów systemu

## Instalacja i uruchomienie bazy danych Postgresql
```
sudo apt-get install postgresql postgis
```
Wyedytuj /etc/postgresql/17/main/postgresql.conf - zmień listen_adressses na *, następnie w pg_hba.conf zezwól na dostęp z adresów innych niż 127.0.0.1.

```
sudo service postgresql restart
sudo su postgres
createdb papierowygis
createuser -P -s -e lesnik
```
## Instalacja QGIS Server
Uzupełnij /etc/apt/sources.list.d/qgis.sources zgodnie z instrukcją na stronie qgis.org
```
sudo wget -O /etc/apt/keyrings/qgis-archive-keyring.gpg https://download.qgis.org/downloads/qgis-archive-keyring.gpg
sudo apt-get update
sudo apt-get install qgis qgis-server
```

## Instalacja Python3 Virtual Enviroment i serwera www py-qgis-server
```
sudo apt-get install python3-venv python3-psutil
set -e
sudo python3 -m venv /opt/local/py-qgis-server --system-site-packages
sudo /opt/local/py-qgis-server/bin/pip install -U pip setuptools wheel pysocks typing py-qgis-server
```
### Konfiguracja
```
sudo mkdir -p /srv/qgis/plugins /srv/qgis/config /srv/data /var/log/qgis /var/lib/py-qgis-server
sudo touch /var/lib/py-qgis-server/py-qgis-restartmon
sudo chmod 664 /var/lib/py-qgis-server/py-qgis-restartmon
```
Utwórz plik /usr/bin/qgis-reload - szablon w /ustawienia/qgis-reload a następnie uczyń go wykonywalnym
```
chmod 750 /usr/bin/qgis-reload
```
Utwórz /srv/qgis/server.conf - szablon w /ustawienia/server.conf (zwróć uwagę poniżej)

```
QGIS Server will be available at http://127.0.0.1:7200/ows/
the plugins are installed in /srv/qgis/plugins (pluginpath). See QGIS Server plugins.
the file to watch for restarting workers is /var/lib/py-qgis-server/py-qgis-restartmon (restartmon).
the directory containing the projects to be published /srv/data (rootdir). The projects must be in sub-folders.
Lizmap QGIS Server API is enabled
```



## Instalacja PHP
```
apt install php8.4-cli php8.4-fpm php8.4-bz2 php8.4-curl php8.4-gd php8.4-intl php8.4-mbstring php8.4-pgsql php8.4-sqlite3 php8.4-xml php8.4-ldap php8.4-redis
```
Teraz przygotuj plik lizmap.conf, wyedytuj ustawienia a następnie zrób symlink
