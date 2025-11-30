# Wpisy do skonfigurowania dla logowania przez Postgresql
[jdb:jauth]
driver=pgsql
host=localhost
port=5432
database="your_database"
user=my_login
password=my_password
search_path=public

[jdb:lizlog]
driver=pgsql
host=localhost
port=5432
database="your_database"
user=my_login
password=my_password
search_path=public

# Wersja z usługą Postgresql

[jdb:jauth]
driver=pgsql
service=my_service
database="your_database"
search_path=lizmap,public

[jdb:lizlog]
driver=pgsql
service=my_service
database="your_database"
search_path=lizmap,public
