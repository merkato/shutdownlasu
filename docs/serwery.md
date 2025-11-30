# configure locales
locale-gen pl_PL.UTF-8 #replace fr with your language
dpkg-reconfigure locales
# u mnie nie poszło
# define your timezone [useful for logs]
dpkg-reconfigure tzdata
sudo apt-get install ntp ntpdate

# Instalacja nginx
sudo apt-get update # update packages list
sudo apt-get install curl openssl libssl3 nginx-full nginx nginx-common
sudo apt-get install spawn-fcgi
# Instalacja PHP
apt install php8.4-cli php8.4-fpm php8.4-bz2 php8.4-curl php8.4-gd php8.4-intl php8.4-mbstring php8.4-pgsql php8.4-sqlite3 php8.4-xml php8.4-ldap php8.4-redis

# Teraz przygotuj plik lizmap.conf, wyedytuj ustawienia a następnie zrób symlink

sudo ln -s /etc/nginx/sites-available/lizmap.conf /etc/nginx/sites-enabled/lizmap.conf
# Gotowi do wystartowania nginx
sudo service nginx restart

# Postgresql
sudo apt-get install postgresql postgis
# Tu coś poszło nie tak. Poszukamy do czego tego potrzebujemy
sudo apt-get install postgresql-contrib  pgtune

# Miejsce na dane serwera QGIS
mkdir /home/data
mkdir /home/data/cache/
