# Wstępna konfiguracja systemu
```
locale-gen pl_PL.UTF-8 #replace fr with your language
dpkg-reconfigure locales
```
Ten element u mnie nie poszedł
Następnie konfiguracja pod logi
```
dpkg-reconfigure tzdata
sudo apt-get install ntp ntpdate
```

# Instalacja serwera HTTP 
```
sudo apt-get update # update packages list
sudo apt-get install curl openssl libssl3
sudo apt-get install python3-venv python3-psutil
```

## Instalacja PHP
```
apt install php8.4-cli php8.4-fpm php8.4-bz2 php8.4-curl php8.4-gd php8.4-intl php8.4-mbstring php8.4-pgsql php8.4-sqlite3 php8.4-xml php8.4-ldap php8.4-redis
```
Teraz przygotuj plik lizmap.conf, wyedytuj ustawienia a następnie zrób symlink

```
sudo ln -s /etc/nginx/sites-available/lizmap.conf /etc/nginx/sites-enabled/lizmap.conf
```

Gotowi do wystartowania nginx
```
sudo service nginx restart
```

# Instalacja i uruchomienie bazy danych Postgresql
```
sudo apt-get install postgresql postgis
```

Tu coś poszło nie tak. Poszukamy do czego tego potrzebujemy
```
sudo apt-get install postgresql-contrib  pgtune
```

Miejsce na dane serwera QGIS
```
mkdir /home/data
mkdir /home/data/cache/
```
## Instalacja QGIS Server
```
sudo wget -O /etc/apt/keyrings/qgis-archive-keyring.gpg https://download.qgis.org/downloads/qgis-archive-keyring.gpg
# Uzupełnij /etc/apt/sources.list.d/qgis.sources
sudo apt-get update
sudo apt-get install qgis-server
```

```
VERSION=3.9.0
LOCATION=/var/www

cd $LOCATION
wget https://github.com/3liz/lizmap-web-client/releases/download/$VERSION/lizmap-web-client-$VERSION.zip
# Unzip archive
unzip lizmap-web-client-$VERSION.zip

# virtual link for http://localhost/lizmap/
ln -s $LOCATION/lizmap-web-client-$VERSION/lizmap/www/ /var/www/html/lizmap
# Remove archive
rm lizmap-web-client-$VERSION.zip
# Tworzenie profili
cd lizmap/var/config
cp profiles.ini.php.dist profiles.ini.php
cd ../../..

# Wskaż katalog na przechowywanie projektów
chown :www-data /path/to/a/lizmap_directory -R
chmod 775 /path/to/a/lizmap_directory -R

cd /var/www/lizmap-web-client-$VERSION/
lizmap/install/set_rights.sh www-data www-data

# Tworzenie plików konfiguracji. Należy zmienić url do QGIS servera na własny
cd lizmap/var/config
cp lizmapConfig.ini.php.dist lizmapConfig.ini.php
cp localconfig.ini.php.dist localconfig.ini.php
cd ../../..

php lizmap/install/installer.php
```
