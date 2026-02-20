import os
import re
import zipfile
import tempfile
import shutil
import urllib.request
from qgis.utils import iface
from qgis.PyQt.QtWidgets import (QDockWidget, QVBoxLayout, QWidget, QFormLayout, 
                               QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, 
                               QHBoxLayout, QFileDialog, QDialog, QLabel, QListWidget, 
                               QListWidgetItem, QMessageBox, QComboBox, QSpinBox, QScrollArea)
from qgis.PyQt.QtCore import Qt, QDate, QSize, QTimer, QRectF
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QPen
from qgis.gui import QgsCollapsibleGroupBox, QgsRubberBand
from qgis.core import (QgsProject, QgsFeature, QgsGeometry, QgsFeatureRequest, 
                       Qgis, QgsDefaultValue, QgsLayoutExporter, QgsCoordinateReferenceSystem,
                       QgsVectorFileWriter, QgsExpressionContextUtils, QgsPointXY, QgsVectorLayer,
                       QgsMasterLayoutInterface)

# --- BLOK KONFIGURACYJNY ---
CONFIG = {
    'layers': {
        'parcels': 'Działki ewidencyjne',
        'usages': 'Użytki gruntowe',
        'exclusions': 'Wyłączenia z produkcji',
        'inspections': 'Lustracje terenowe',
        'reports': 'Wykonanie lustracji',
        'forest_districts': 'Nadleśnictwa'  # Nowa warstwa
    },
    'fields': {
        'parcel_key': 'klucz_dzialki',
        'exclusion_key': 'klucz_wylaczenia',
        'usage_no': 'nrkolejny',
        'usage_ref': 'nrkolejnyug',
        'inspection_ref': 'nrkolejnyko',
        'expiry_date': 'obiekt_koniec',
        'area_field': 'pow_ewid',
        'actual_area': 'pow_faktyczna',
        'purpose': 'cel_wylaczenia',
        'violation': 'naruszenie',
        'inspection_date': 'data_lustracji',
        'district_name': 'nazwa_nadl',
        'district_office': 'siedziba'
    }
}

attr_dock = None
area_hud = None

# --- KLASA HUD (LICZNIK POWIERZCHNI) ---

class SmartAreaHUD:
    """Licznik powierzchni ha reagujący na kursor i edycję węzłów."""
    
    def __init__(self, canvas):
        self.canvas = canvas
        self.label = QLabel(self.canvas)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.label.setStyleSheet("""
            background-color: rgba(30, 30, 30, 220);
            color: #00ff00;
            border: 1px solid #ffffff;
            border-radius: 4px;
            padding: 5px;
            font-family: 'Consolas', monospace;
            font-size: 10pt;
            font-weight: bold;
        """)
        self.label.hide()
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_logic)
        self.timer.start(100)

    def refresh_logic(self):
        try:
            lyr = self.canvas.currentLayer()
            if not lyr:
                self.label.hide()
                return
            
            valid_names = [CONFIG['layers']['usages'], CONFIG['layers']['exclusions']]
            if lyr.name() not in valid_names:
                self.label.hide()
                return

            geom = None
            tool = self.canvas.mapTool()

            # Tryb 1: Rysowanie nowej geometrii
            if hasattr(tool, 'captureCurve'):
                curve = tool.captureCurve()
                if curve:
                    pts = [QgsPointXY(p) for p in curve.points()]
                    cursor_pos = self.canvas.mouseLastXY()
                    map_pt = self.canvas.getCoordinateTransform().toMapCoordinates(
                        cursor_pos.x(), cursor_pos.y()
                    )
                    pts.append(QgsPointXY(map_pt))
                    if len(pts) >= 3:
                        geom = QgsGeometry.fromPolygonXY([pts])

            # Tryb 2: Edycja węzłów (RubberBand)
            if not geom:
                for item in self.canvas.items():
                    if isinstance(item, QgsRubberBand):
                        temp_geom = item.asGeometry()
                        if temp_geom and temp_geom.type() == Qgis.GeometryType.Polygon:
                            geom = temp_geom
                            break

            if not geom or geom.isEmpty():
                self.label.hide()
                return

            area_ha = geom.area() / 10000.0
            screen_pt = self.canvas.getCoordinateTransform().transform(geom.centroid().asPoint())
            
            self.label.setText(f"{area_ha:.4f} ha")
            self.label.adjustSize()
            self.label.move(
                int(screen_pt.x() - self.label.width() / 2),
                int(screen_pt.y() - self.label.height() / 2)
            )
            self.label.show()
        except Exception:
            self.label.hide()

    def stop(self):
        self.timer.stop()
        self.label.deleteLater()


# --- DIALOGI ---

class DialogRaportu(QDialog):
    """Okno parametrów dla batchowego eksportu PDF."""
    
    def __init__(self, nadl_list, layout_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Parametry PDF: {layout_name}")
        self.setMinimumSize(450, 550)
        layout = QVBoxLayout(self)
        
        group = QgsCollapsibleGroupBox("Okres i rok sprawozdawczy")
        form = QFormLayout(group)
        self.cb_rok = QSpinBox()
        self.cb_rok.setRange(2020, 2045)
        self.cb_rok.setValue(QDate.currentDate().year())
        self.cb_pol = QComboBox()
        self.cb_pol.addItems(["I", "II"])
        form.addRow("Rok:", self.cb_rok)
        form.addRow("Półrocze:", self.cb_pol)
        layout.addWidget(group)

        self.lw = QListWidget()
        for n in sorted(nadl_list):
            if n and str(n) != 'NULL':
                item = QListWidgetItem(str(n))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                self.lw.addItem(item)
        
        layout.addWidget(QLabel("Wybierz Nadleśnictwa:"))
        layout.addWidget(self.lw)
        
        btn_box = QHBoxLayout()
        b_all = QPushButton("Wszystkie")
        b_none = QPushButton("Żadne")
        btn_box.addWidget(b_all)
        btn_box.addWidget(b_none)
        layout.addLayout(btn_box)
        
        self.btn_run = QPushButton("Generuj raporty PDF")
        self.btn_run.setStyleSheet("font-weight: bold; background-color: #e1f5fe; padding: 10px;")
        layout.addWidget(self.btn_run)

        b_all.clicked.connect(self.select_all)
        b_none.clicked.connect(self.select_none)
        self.btn_run.clicked.connect(lambda: self.done(10))

    def select_all(self):
        for i in range(self.lw.count()):
            self.lw.item(i).setCheckState(Qt.Checked)

    def select_none(self):
        for i in range(self.lw.count()):
            self.lw.item(i).setCheckState(Qt.Unchecked)

    def get_params(self):
        selected = []
        for i in range(self.lw.count()):
            if self.lw.item(i).checkState() == Qt.Checked:
                selected.append(self.lw.item(i).text())
        return {
            'rok': self.cb_rok.value(),
            'polrocze': self.cb_pol.currentText(),
            'nadlesnictwa': selected
        }


# --- PANEL GŁÓWNY ---

class PanelWylaczenia(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_glowny = QVBoxLayout(self)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        container = QWidget()
        self.lyt = QVBoxLayout(container)
        self.lyt.setContentsMargins(5, 5, 5, 5)
        self.lyt.setSpacing(10)
        
        # 1. POBIERANIE (ULDK)
        self.group_import = QgsCollapsibleGroupBox("1. Pobieranie i wyszukiwanie")
        fl_imp = QFormLayout(self.group_import)
        self.in_teryt = QLineEdit()
        self.in_znak = QLineEdit()
        self.in_pow = QLineEdit()
        
        self.in_teryt.setPlaceholderText("TERYT np. 140607_2.0001.123")
        self.in_znak.setPlaceholderText("Znak np. ZS.224.2.310.2025")
        self.in_pow.setPlaceholderText("Powierzchnia opisowa (ha)")
        self.btn_get_teryt_map = QPushButton("Pobierz TERYT z mapy")
        self.btn_get_teryt_map.setStyleSheet("background-color: #e1f5fe; font-weight: bold;")
        self.btn_get_teryt_map.clicked.connect(self.activate_teryt_tool)
        fl_imp.addRow("TERYT:", self.in_teryt)
        fl_imp.addRow("Znak sprawy:", self.in_znak)
        fl_imp.addRow("Pow. (ha):", self.in_pow)
        fl_imp.insertRow(1, "Lokalizuj:", self.btn_get_teryt_map)
        self.btn_uldk = QPushButton("Pobierz z ULDK (+Automat Ls)")
        self.btn_uldk.setStyleSheet("background-color: #d1ffd1; font-weight: bold; padding: 5px;")
        fl_imp.addRow(self.btn_uldk)
        
        self.in_search = QLineEdit()
        self.in_search.setPlaceholderText("Szukaj po TERYT lub Znaku...")
        fl_imp.addRow("Szukaj:", self.in_search)
        self.btn_search = QPushButton("Wczytaj sprawę do drzewa")
        fl_imp.addRow(self.btn_search)
        self.lyt.addWidget(self.group_import)

        # 2. ZARZĄDZANIE STRUKTURĄ
        self.group_main = QgsCollapsibleGroupBox("2. Zarządzanie strukturą i naruszenia")
        lyt_main = QVBoxLayout(self.group_main)
        
        self.btn_fetch = QPushButton("Wczytaj zaznaczenie z mapy")
        self.btn_fetch.setStyleSheet("background-color: #e3f2fd; font-weight: bold;")
        lyt_main.addWidget(self.btn_fetch)
        
        # Kontrola drzewa
        row_tree_ctrl = QHBoxLayout()
        self.btn_expand = QPushButton("Rozwiń wszystko + Zoom")
        self.btn_collapse = QPushButton("Zwiń wszystko")
        row_tree_ctrl.addWidget(self.btn_expand)
        row_tree_ctrl.addWidget(self.btn_collapse)
        lyt_main.addLayout(row_tree_ctrl)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Obiekt", "Atrybuty i Alerty"])
        self.tree.setColumnWidth(0, 240)
        self.tree.setMinimumHeight(400)
        lyt_main.addWidget(self.tree)
        
        row_edit = QHBoxLayout()
        self.btn_edit = QPushButton("Edytuj")
        self.btn_add = QPushButton("DODAJ POTOMNY")
        self.btn_add.setStyleSheet("background-color: #fff9c4; font-weight: bold;")
        row_edit.addWidget(self.btn_edit)
        row_edit.addWidget(self.btn_add)
        lyt_main.addLayout(row_edit)
        
        self.btn_excl = QPushButton("Utwórz wyłączenie z geometrii użytku")
        lyt_main.addWidget(self.btn_excl)
        
        self.btn_save = QPushButton("ZAPISZ WSZYSTKO")
        self.btn_save.setStyleSheet("background-color: #d1ffd1; font-weight: bold; padding: 8px;")
        lyt_main.addWidget(self.btn_save)
        
        self.lyt.addWidget(self.group_main)

        # 3. EKSPORTY
        self.group_tools = QgsCollapsibleGroupBox("3. Eksport ZIP (mLas) i XLS")
        self.group_tools.setCollapsed(True)
        lyt_tools = QVBoxLayout(self.group_tools)
        self.btn_mlas = QPushButton("Eksportuj paczkę ZIP (mLasInżynier)")
        self.btn_xls = QPushButton("Eksportuj tabelę raportową do XLS")
        lyt_tools.addWidget(self.btn_mlas)
        lyt_tools.addWidget(self.btn_xls)
        self.lyt.addWidget(self.group_tools)

        # 4. RAPORTY PDF
        self.group_rep = QgsCollapsibleGroupBox("4. Raporty PDF (Batch)")
        self.group_rep.setCollapsed(True)
        lyt_rep = QVBoxLayout(self.group_rep)
        self.btn_rep1 = QPushButton("Generuj Załącznik nr 1")
        self.btn_rep3 = QPushButton("Generuj Załącznik nr 3")
        lyt_rep.addWidget(self.btn_rep1)
        lyt_rep.addWidget(self.btn_rep3)
        self.lyt.addWidget(self.group_rep)
        # Sekcja 5: Aktualizacja i konfiguracja
        #self.group_config = QgsCollapsibleGroupBox("5. Aktualizacja i konfiguracja")
        #self.group_config.setCollapsed(True)
        #lyt_config = QVBoxLayout(self.group_config)
        
        #self.btn_refresh_mview = QPushButton("Odśwież tabele stanu wykonania lustracji")
        #self.btn_refresh_mview.setStyleSheet("background-color: #fce4ec; font-weight: bold; padding: 8px;")
        #self.btn_refresh_mview.clicked.connect(lambda: self.refresh_pg_materialized_view("wylaczenia.v_do_lustracji_biezace_polrocze"))
        #self.btn_refresh_mv = QPushButton("Odśwież tabele lusttracji do raportów")
        #self.btn_refresh_mv.setStyleSheet("background-color: #fce4ec; font-weight: bold; padding: 8px;")
        #self.btn_refresh_mv.clicked.connect(lambda: self.refresh_pg_materialized_view("wylaczenia.v_kontury_z_lustracja"))
        
        #lyt_config.addWidget(self.btn_refresh_mview)
        #lyt_config.addWidget(self.btn_refresh_mv)
        #self.lyt.addWidget(self.group_config)
        
        # Stopka
        self.lyt.addStretch()
        self.lbl_foot = QLabel("Opracowanie na zlecenie RDLP w Radomiu\nTomasz Nycz, GIS w Górach, 2026")
        self.lbl_foot.setAlignment(Qt.AlignCenter)
        self.lbl_foot.setStyleSheet("color: #424242; font-size: 10px; font-weight: bold; padding: 10px;")
        self.lyt.addWidget(self.lbl_foot)

        # Sygnały
        self.btn_uldk.clicked.connect(self.run_uldk)
        self.btn_search.clicked.connect(self.search_parcel)
        self.btn_fetch.clicked.connect(self.fetch_manual_data)
        self.btn_expand.clicked.connect(self.expand_and_zoom_tree)
        self.btn_collapse.clicked.connect(self.tree.collapseAll)
        self.btn_add.clicked.connect(self.add_child_to_selected)
        self.btn_edit.clicked.connect(self.edit_selected_item)
        self.btn_excl.clicked.connect(self.create_exclusion_from_usage)
        self.btn_save.clicked.connect(self.save_all_changes)
        self.btn_mlas.clicked.connect(self.export_mlas_pack)
        self.btn_xls.clicked.connect(self.export_to_xls)
        self.btn_rep1.clicked.connect(lambda: self.run_report_flow('Załącznik nr 1'))
        self.btn_rep3.clicked.connect(lambda: self.run_report_flow('Załącznik nr 3'))
        self.tree.itemClicked.connect(self.flash_selected_feature)

        scroll.setWidget(container)
        self.layout_glowny.addWidget(scroll)

    # --- METODY POMOCNICZE ---
    def activate_teryt_tool(self):
        """Włącza narzędzie wyboru punktu na mapie."""
        from qgis.gui import QgsMapToolEmitPoint
        
        self.canvas = iface.mapCanvas()
        self.teryt_tool = QgsMapToolEmitPoint(self.canvas)
        self.teryt_tool.canvasClicked.connect(self.handle_map_click_for_teryt)
        self.canvas.setMapTool(self.teryt_tool)
        iface.messageBar().pushMessage("ULDK", "Kliknij wewnątrz działki, aby pobrać TERYT", level=Qgis.Info, duration=3)
    
    def handle_map_click_for_teryt(self, point):
        """
        Pobiera TERYT z GUGiK na podstawie kliknięcia, obsługując wieloliniowe 
        odpowiedzi serwera i czyszcząc techniczne statusy.
        """
        import urllib.request
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
        
        # Powrót do standardowego narzędzia (rączki) po kliknięciu
        self.canvas.unsetMapTool(self.teryt_tool)
        
        # 1. Transformacja współrzędnych do EPSG:2180 (standard ULDK)
        crs_src = self.canvas.mapSettings().destinationCrs()
        crs_2180 = QgsCoordinateReferenceSystem("EPSG:2180")
        transform = QgsCoordinateTransform(crs_src, crs_2180, QgsProject.instance())
        
        pt_2180 = transform.transform(point)
        
        # Debugowanie współrzędnych w konsoli QGIS
        print(f"DEBUG: Kliknięcie w {crs_src.authid()} -> 2180: X={pt_2180.x()}, Y={pt_2180.y()}")
        
        try:
            # 2. Budowa URL zgodnie z dokumentacją GetParcelByXY
            url = (
                f"https://uldk.gugik.gov.pl/?request=GetParcelByXY"
                f"&xy={pt_2180.x()},{pt_2180.y()}"
                f"&result=id"
            )
            
            print(f"DEBUG: Wysłany URL: {url}")
            
            # 3. Pobranie i parsowanie odpowiedzi
            res = urllib.request.urlopen(url, timeout=5).read().decode('utf-8')
            
            # Rozbicie na linie i usunięcie pustych (często pierwsza linia to '0', a druga to TERYT)
            lines = [line.strip() for line in res.splitlines() if line.strip()]
            
            print(f"DEBUG: Odpowiedź serwera (linie): {lines}")

            if lines:
                # Interesuje nas ostatnia linia, bo tam ULDK zwraca właściwy identyfikator
                raw_data = lines[-1]
                
                # Usuwamy ewentualną geometrię lub nazwy po średniku/spacji
                clean_teryt = raw_data.split(';')[0].split(' ')[0].strip()
                
                # 4. Walidacja wyniku (ID działki ma zazwyczaj min. 14 znaków)
                if len(clean_teryt) > 5 and not clean_teryt.startswith('-1') and "Error" not in clean_teryt:
                    # Wpisanie do pola tekstowego w widgecie
                    self.in_teryt.setText(clean_teryt)
                    iface.messageBar().pushMessage("ULDK", f"Pobrano TERYT: {clean_teryt}", level=Qgis.Success)
                else:
                    QMessageBox.warning(self, "ULDK", f"Nie odnaleziono poprawnej działki.\nSerwer zwrócił: {raw_data}")
            else:
                QMessageBox.warning(self, "ULDK", "Serwer zwrócił pustą odpowiedź.")
                
        except Exception as e:
            QMessageBox.critical(self, "Błąd krytyczny", f"Błąd zapytania ULDK: {str(e)}")
            
    def refresh_pg_materialized_view(self, view_name):
        """
        Odświeża widok zmaterializowany w PostgreSQL korzystając z pg_service.
        """
        from qgis.core import QgsProject, QgsDataSourceUri
        from qgis.PyQt.QtSql import QSqlDatabase, QSqlQuery
        from qgis.PyQt.QtWidgets import QApplication
        from qgis.core import Qgis

        # Pobranie parametrów połączenia z warstwy 'Działki'
        layers = QgsProject.instance().mapLayersByName('Działki')
        if not layers:
            iface.messageBar().pushMessage("Błąd", "Nie znaleziono warstwy 'Działki' do pobrania usługi!", level=Qgis.Critical)
            return
            
        uri = QgsDataSourceUri(layers[0].source())
        service_name = uri.service()
        
        if not service_name:
            iface.messageBar().pushMessage("Błąd", "Warstwa nie korzysta z pg_service!", level=Qgis.Warning)
            return

        # Zmiana kursora na czas oczekiwania
        QApplication.setOverrideCursor(Qt.WaitCursor)
        
        # Konfiguracja połączenia QtSql przez usługę
        db_name = "refresh_conn"
        if QSqlDatabase.contains(db_name):
            db = QSqlDatabase.database(db_name)
        else:
            db = QSqlDatabase.addDatabase("QPSQL", db_name)
            db.setDatabaseName(service_name)

        try:
            if db.open():
                query = QSqlQuery(db)
                # Próba odświeżenia współbieżnego (wymaga Unique Index w Postgresie)
                success = query.exec_(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name};")
                
                if not success:
                    # Jeśli brak indeksu unikalnego, wykonaj zwykły refresh
                    query.exec_(f"REFRESH MATERIALIZED VIEW {view_name};")
                
                iface.messageBar().pushMessage("Baza danych", f"Pomyślnie odświeżono widok: {view_name}", level=Qgis.Success)
            else:
                QMessageBox.critical(self, "Błąd bazy", f"Nie można połączyć się z usługą {service_name}:\n{db.lastError().text()}")
        finally:
            db.close()
            QSqlDatabase.removeDatabase(db_name)
            QApplication.restoreOverrideCursor()
            
    def sanitize_text(self, text):
        """Standardowa procedura czyszczenia nazw plików i pól (PEP 8)."""
        pl_map = {
            'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 
            'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
            'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N', 
            'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z'
        }
        for char, repl in pl_map.items():
            text = text.replace(char, repl)
        text = text.replace(' ', '_')
        return re.sub(r'[^a-zA-Z0-9_]', '', text).lower()

    def get_val(self, feature, field_name):
        """Pobiera sformatowaną wartość pola (obsługa dat)."""
        idx = feature.fields().indexFromName(field_name)
        if idx == -1:
            return ""
        value = feature.attribute(idx)
        if hasattr(value, 'toString'):
            return value.toString('dd.MM.yyyy')
        if value is None or str(value) == 'NULL':
            return ""
        return str(value)

    # --- LOGIKA BIZNESOWA ---

    def expand_and_zoom_tree(self):
        """Rozwija hierarchię i zoomuje do sumarycznego zasięgu."""
        self.tree.expandAll()
        combined_extent = None
        project = QgsProject.instance()
        
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.UserRole)
            if data and 'layer' in data:
                lyr = project.mapLayersByName(data['layer'])[0]
                feat = lyr.getFeature(data['id'])
                if feat.hasGeometry():
                    if combined_extent is None:
                        combined_extent = feat.geometry().boundingBox()
                    else:
                        combined_extent.combineExtentWith(feat.geometry().boundingBox())
        
        if combined_extent:
            combined_extent.scale(1.15)
            iface.mapCanvas().setExtent(combined_extent)
            iface.mapCanvas().refresh()

    def fetch_manual_data(self):
        """Wczytuje drzewo z kompletnym kolorowaniem (pastelow czerwony/szary)."""
        self.tree.clear()
        today = QDate.currentDate()
        project = QgsProject.instance()
        
        # Pędzle stylizacji
        brush_violation = QBrush(QColor(255, 204, 204))  # Pastelowy czerwony
        brush_ended = QBrush(QColor(240, 240, 240))      # Jasnoszary
        gray_text = QBrush(QColor("#9e9e9e"))
        red_text = QBrush(QColor("#d32f2f"))
        
        try:
            l_par = project.mapLayersByName(CONFIG['layers']['parcels'])[0]
            l_usa = project.mapLayersByName(CONFIG['layers']['usages'])[0]
            l_exc = project.mapLayersByName(CONFIG['layers']['exclusions'])[0]
            l_ins = project.mapLayersByName(CONFIG['layers']['inspections'])[0]
        except IndexError:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono warstw bazy.")
            return

        for dz in l_par.selectedFeatures():
            t, z, k = dz['teryt'], dz['znak_sprawy'], dz[CONFIG['fields']['parcel_key']]
            
            # Poziom 1: Działka
            root = QTreeWidgetItem(self.tree, [f"Działka: {t}", f"Znak: {z}"])
            root.setData(0, Qt.UserRole, {
                'layer': CONFIG['layers']['parcels'], 
                'id': dz.id(), 
                'keys': {'teryt': t, 'znak_sprawy': z, CONFIG['fields']['parcel_key']: k}
            })
            root.setExpanded(True)
            
            # Poziom 2: Użytki
            expr_u = f"\"{CONFIG['fields']['parcel_key']}\"='{k}'"
            for fu in l_usa.getFeatures(QgsFeatureRequest().setFilterExpression(expr_u)):
                nu = fu[CONFIG['fields']['usage_no']]
                p_u = self.get_val(fu, CONFIG['fields']['area_field'])
                it_u = QTreeWidgetItem(root, [f"Użytek nr {nu}", f"Pow: {p_u} ha"])
                it_u.setData(0, Qt.UserRole, {
                    'layer': CONFIG['layers']['usages'], 
                    'id': fu.id(), 
                    'keys': {
                        'teryt': t, 'znak_sprawy': z, 
                        CONFIG['fields']['parcel_key']: k, CONFIG['fields']['usage_no']: nu
                    }
                })
                it_u.setExpanded(True)
                
                # Poziom 3: Wyłączenia
                expr_e = f"\"{CONFIG['fields']['usage_ref']}\"={nu} AND \"teryt\"='{t}'"
                for fe in l_exc.getFeatures(QgsFeatureRequest().setFilterExpression(expr_e)):
                    nk, kw = fe[CONFIG['fields']['usage_no']], fe[CONFIG['fields']['exclusion_key']]
                    p_e = self.get_val(fe, CONFIG['fields']['area_field'])
                    it_e = QTreeWidgetItem(it_u, [f"Wyłączenie nr {nk}", f"Pow: {p_e} ha"])
                    it_e.setData(0, Qt.UserRole, {
                        'layer': CONFIG['layers']['exclusions'], 
                        'id': fe.id(), 
                        'keys': {
                            'teryt': t, 'znak_sprawy': z, 
                            CONFIG['fields']['usage_no']: nk, CONFIG['fields']['exclusion_key']: kw
                        }
                    })
                    it_e.setExpanded(True)
                    
                    is_ended = False
                    exp = fe[CONFIG['fields']['expiry_date']]
                    if exp and not exp.isNull() and exp < today:
                        is_ended = True
                        for col in range(2):
                            it_e.setForeground(col, gray_text)

                    # Poziom 4: Lustracje (PO NOWYM KLUCZU)
                    expr_i = f"\"{CONFIG['fields']['exclusion_key']}\"='{kw}'"
                    for fi in l_ins.getFeatures(QgsFeatureRequest().setFilterExpression(expr_i)):
                        dl = self.get_val(fi, CONFIG['fields']['inspection_date'])
                        pf = self.get_val(fi, CONFIG['fields']['actual_area'])
                        ce = self.get_val(fi, CONFIG['fields']['purpose'])
                        na = fi[CONFIG['fields']['violation']]
                        
                        violation = False
                        if na is True or str(na).lower() in ['true', 't', '1']:
                            violation = True
                        
                        txt_l = f"Lustracja z dnia {dl}" if dl else "Lustracja"
                        txt_a = f"Pow: {pf} ha | Cel: {ce} | Naruszenie: {'TAK' if violation else 'NIE'}"
                        
                        it_i = QTreeWidgetItem(it_e, [txt_l, txt_a])
                        it_i.setData(0, Qt.UserRole, {
                            'layer': CONFIG['layers']['inspections'], 
                            'id': fi.id()
                        })
                        
                        # STYLIZACJA RZĘDU
                        if is_ended:
                            for col in range(2):
                                it_i.setBackground(col, brush_ended)
                                it_i.setForeground(col, gray_text)
                        elif violation:
                            for col in range(2):
                                it_i.setBackground(col, brush_violation)
                            it_i.setForeground(1, red_text)
                            font = it_i.font(1)
                            font.setBold(True)
                            it_i.setFont(1, font)

    def run_uldk(self):
        """Pobiera geometrię z ULDK i tworzy strukturę obiektów korzystając z CONFIG."""
        # 1. Pobranie i walidacja danych wejściowych
        t = self.in_teryt.text().strip()
        z = self.in_znak.text().strip()
        p_raw = self.in_pow.text().replace(',', '.').strip()

        if not t or not z or not p_raw:
            QMessageBox.warning(self, "Brak danych", "Wypełnij TERYT, Znak sprawy i Powierzchnię opisową.")
            return

        try:
            pow_val = float(p_raw)
            
            # 2. Zapytanie do serwera GUGiK
            url = f"https://uldk.gugik.gov.pl/?request=GetParcelById&id={t}&result=geom_wkt"
            res = urllib.request.urlopen(url, timeout=10).read().decode('utf-8')
            
            if ';' not in res:
                QMessageBox.warning(self, "Błąd ULDK", f"Serwer nie zwrócił geometrii dla działki: {t}")
                return

            geom = QgsGeometry.fromWkt(res.split(';')[-1].strip())
            if geom.isEmpty():
                QMessageBox.warning(self, "Błąd geometrii", "Pobrana geometria jest pusta.")
                return

            project = QgsProject.instance()

            # 3. Bezpieczne pobieranie warstw ze słownika CONFIG
            l_parcels_list = project.mapLayersByName(CONFIG['layers']['parcels'])
            l_usages_list = project.mapLayersByName(CONFIG['layers']['usages'])

            if not l_parcels_list or not l_usages_list:
                missing = []
                if not l_parcels_list: missing.append(CONFIG['layers']['parcels'])
                if not l_usages_list: missing.append(CONFIG['layers']['usages'])
                QMessageBox.critical(self, "Błąd warstw", f"Nie znaleziono w projekcie warstw:\n- " + "\n- ".join(missing))
                return

            l_dz = l_parcels_list[0]
            l_uz = l_usages_list[0]

            # 4. Wymuszenie widoczności (nowy standard)
            root = project.layerTreeRoot()
            for lyr in [l_dz, l_uz]:
                node = root.findLayer(lyr.id())
                if node:
                    node.setItemVisibilityChecked(True)
                    if node.parent(): node.parent().setItemVisibilityChecked(True)

            # 5. Operacja na warstwie Działek
            if not l_dz.isEditable(): l_dz.startEditing()
            
            f_dz = QgsFeature(l_dz.fields())
            f_dz.setGeometry(geom)
            f_dz['teryt'] = t
            f_dz['znak_sprawy'] = z
            f_dz[CONFIG['fields']['area_field']] = pow_val
            
            if l_dz.addFeature(f_dz):
                l_dz.commitChanges()
                
                # 6. Pobranie klucza nowej działki (UUID/Serial z bazy)
                req = QgsFeatureRequest().setFilterExpression(f"\"teryt\"='{t}' AND \"znak_sprawy\"='{z}'")
                dz_f = next(l_dz.getFeatures(req), None)
                
                if dz_f:
                    # 7. Operacja na warstwie Użytków
                    if not l_uz.isEditable(): l_uz.startEditing()
                    
                    f_uz = QgsFeature(l_uz.fields())
                    f_uz.setGeometry(geom)
                    # Powiązanie przez klucz ze słownika
                    f_uz[CONFIG['fields']['parcel_key']] = dz_f[CONFIG['fields']['parcel_key']]
                    f_uz['teryt'] = t
                    f_uz['znak_sprawy'] = z
                    f_uz['koduzytku'] = 'Ls' # Zgodnie z ustaleniami: zawsze Ls
                    
                    if l_uz.addFeature(f_uz):
                        l_uz.commitChanges()
                        
                        # 8. Finalizacja widoku
                        iface.mapCanvas().setExtent(geom.boundingBox())
                        iface.mapCanvas().refresh()
                        l_dz.selectByIds([dz_f.id()])
                        self.fetch_manual_data() # Odświeżenie drzewa w panelu
                        
                        iface.messageBar().pushMessage("Sukces", "Pobrano działkę i utworzono użytek Ls.", level=Qgis.Success)
                else:
                    QMessageBox.warning(self, "Błąd relacji", "Działka została dodana, ale nie udało się pobrać jej klucza do stworzenia użytku.")
            
        except Exception as e:
            # Tutaj już nie będzie "index out of range", chyba że brakuje pola w CONFIG
            QMessageBox.critical(self, "Błąd krytyczny", f"Szczegóły błędu:\n{str(e)}")
        
    def add_child_to_selected(self):
        """
        Tworzy relację nadrzędny-potomny, automatycznie uzupełnia klucze 
        i włącza widoczność warstwy docelowej przed dodaniem obiektu.
        """
        item = self.tree.currentItem()
        if not item:
            iface.messageBar().pushMessage("Błąd", "Wybierz najpierw obiekt w drzewie!", level=Qgis.Warning)
            return
        
        data = item.data(0, Qt.UserRole)
        pk = data['keys'] # Pobranie kluczy (teryt, znak_sprawy, nrkolejny itd.)
        parent_layer_name = data['layer']
        
        # 1. Walidacja: Blokada dodawania lustracji do wygasłych wyłączeń (v64.0)
        if parent_layer_name == 'Kontury wyłączeń':
            lyr_ex = QgsProject.instance().mapLayersByName('Kontury wyłączeń')[0]
            feat = lyr_ex.getFeature(data['id'])
            today = QDate.currentDate()
            if feat['obiekt_koniec'] and not feat['obiekt_koniec'].isNull() and feat['obiekt_koniec'] < today:
                QMessageBox.warning(
                    self, 
                    "Błąd", 
                    "Dla tego wyłączenia zakończono obowiązek lustracji. "
                    "Jeśli stwierdziłeś naruszenie, najpierw zmień daty w wyłączeniu"
                )
                return

        # 2. Definicja mapowania warstw
        mapping = {
            'Działki': 'Użytki', 
            'Użytki': 'Wyłączenia z produkcji', 
            'Wyłączenia z produkcji': 'Lustracje terenowe'
        }
        target_name = mapping.get(parent_layer_name)
        
        if not target_name:
            return

        # 3. Pobranie warstwy docelowej
        layers = QgsProject.instance().mapLayersByName(target_name)
        if not layers:
            iface.messageBar().pushMessage("Błąd", f"Nie znaleziono warstwy: {target_name}", level=Qgis.Critical)
            return
        target_lyr = layers[0]

        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(target_lyr.id())
        if node:
            # Wymuszenie widoczności samej warstwy
            node.setItemVisibilityChecked(True)
            # Wymuszenie widoczności grupy nadrzędnej, jeśli warstwa jest schowana w grupie
            if node.parent():
                node.parent().setItemVisibilityChecked(True)
        # ----------------------------------------------

        # 4. Przygotowanie warstwy do edycji
        iface.setActiveLayer(target_lyr)
        if not target_lyr.isEditable():
            target_lyr.startEditing()

        # Funkcja pomocnicza do ustawiania wartości domyślnych
        def set_def(field_name, value):
            idx = target_lyr.fields().indexFromName(field_name)
            if idx != -1:
                target_lyr.setDefaultValueDefinition(idx, QgsDefaultValue(f"'{value}'"))

        # 5. Automatyczne uzupełnianie kluczy relacji
        set_def('teryt', pk.get('teryt', ''))
        set_def('znak_sprawy', pk.get('znak_sprawy', ''))
        
        if target_name == 'Użytki':
            set_def('klucz_dzialki', pk.get('klucz_dzialki', ''))
            set_def('koduzytku', 'Ls') # Twoje domyślne ustawienie
        elif target_name == 'Kontury wyłączeń':
            set_def('klucz_dzialki', pk.get('klucz_dzialki', ''))
            set_def('nrkolejnyug', pk.get('nrkolejny', ''))
        elif target_name == 'Lustracje terenowe':
            set_def('nrkolejnyko', pk.get('nrkolejny', ''))

        # 6. Uruchomienie narzędzia dodawania obiektu
        iface.actionAddFeature().trigger()
    
    def create_exclusion_from_usage(self):
        """Szybkie kopiowanie geometrii z użytku do nowego wyłączenia."""
        item = self.tree.currentItem()
        if not item or item.data(0, Qt.UserRole)['layer'] != CONFIG['layers']['usages']:
            return
        data = item.data(0, Qt.UserRole)
        pk = data['keys']
        prj = QgsProject.instance()
        l_u = prj.mapLayersByName(CONFIG['layers']['usages'])[0]
        l_e = prj.mapLayersByName(CONFIG['layers']['exclusions'])[0]
        
        feat_u = l_u.getFeature(data['id'])
        iface.setActiveLayer(l_e)
        if not l_e.isEditable():
            l_e.startEditing()
            
        nf = QgsFeature(l_e.fields())
        nf.setGeometry(feat_u.geometry())
        nf['teryt'] = pk['teryt']
        nf['znak_sprawy'] = pk['znak_sprawy']
        nf[CONFIG['fields']['usage_ref']] = pk[CONFIG['fields']['usage_no']]
        nf[CONFIG['fields']['parcel_key']] = pk[CONFIG['fields']['parcel_key']]
        if l_e.addFeature(nf):
            iface.openFeatureForm(l_e, nf)

    def export_mlas_pack(self):
        """Workflow mLas: ZIP """
        if self.tree.topLevelItemCount() == 0:
            return
            
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz ZIP mLas", "", "ZIP (*.zip)")
        if not path:
            return
        if not path.lower().endswith('.zip'):
            path += '.zip'
        
        ids = {CONFIG['layers']['parcels']: [], CONFIG['layers']['usages']: [], CONFIG['layers']['exclusions']: []}
        def walk(n):
            d = n.data(0, Qt.UserRole)
            if d and d['layer'] in ids:
                ids[d['layer']].append(d['id'])
            for i in range(n.childCount()):
                walk(n.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
            
        tmp = tempfile.mkdtemp()
        prj = QgsProject.instance()
        try:
            for ln, fids in ids.items():
                if not fids:
                    continue
                orig = prj.mapLayersByName(ln)[0]
                safe_fn = self.sanitize_text(ln)
                shp_p = os.path.join(tmp, f"{safe_fn}.shp")
                
                # Materializacja i sanitacja nagłówków pól
                mem = orig.materialize(QgsFeatureRequest().setFilterFids(fids))
                mem.startEditing()
                for fld in mem.fields():
                    idx = mem.fields().indexFromName(fld.name())
                    safe_f = self.sanitize_text(fld.name())[:10]
                    mem.renameAttribute(idx, safe_f)
                mem.commitChanges()
                
                opt = QgsVectorFileWriter.SaveVectorOptions()
                opt.driverName, opt.destCRS = "ESRI Shapefile", QgsCoordinateReferenceSystem("EPSG:2180")
                QgsVectorFileWriter.writeAsVectorFormatV3(mem, shp_p, prj.transformContext(), opt)
            
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(tmp):
                    for f in files:
                        z.write(os.path.join(root, f), arcname=f)
            iface.messageBar().pushMessage("Sukces", "Paczka mLas ZIP gotowa.", level=Qgis.Success)
        finally:
            shutil.rmtree(tmp)

    def export_to_xls(self):
        """Eksport raportu do Excel."""
        l_r = QgsProject.instance().mapLayersByName(CONFIG['layers']['reports'])[0]
        path, _ = QFileDialog.getSaveFileName(self, "Eksport XLS", "", "Excel (*.xlsx)")
        if path:
            if not path.lower().endswith('.xlsx'):
                path += '.xlsx'
            opt = QgsVectorFileWriter.SaveVectorOptions()
            opt.driverName = "XLSX"
            QgsVectorFileWriter.writeAsVectorFormatV3(l_r, path, QgsProject.instance().transformContext(), opt)

    def run_report_flow(self, layout_name):
        """Dostosowana logika raportowania z pobieraniem siedziby z warstwy nadleśnictw."""
        l_rap = QgsProject.instance().mapLayersByName(CONFIG['layers']['reports'])[0]
        l_nadl = QgsProject.instance().mapLayersByName(CONFIG['layers']['forest_districts'])[0]
        
        # Pobieranie unikalnych nazw nadleśnictw do dialogu
        idx_name = l_rap.fields().indexFromName(CONFIG['fields']['district_name'])
        dlg = DialogRaportu(l_rap.uniqueValues(idx_name), layout_name, self)
        
        if dlg.exec_():
            p = dlg.get_params()
            project = QgsProject.instance()
            
            # Ustawienie zmiennych globalnych okresu
            QgsExpressionContextUtils.setProjectVariable(project, 'rok', p['rok'])
            QgsExpressionContextUtils.setProjectVariable(project, 'polrocze', p['polrocze'])
            
            dir_p = QFileDialog.getExistingDirectory(self, "Folder PDF")
            if dir_p:
                lo = project.layoutManager().layoutByName(layout_name)
                for nadl in p['nadlesnictwa']:
                    # 1. Pobieranie atrybutu siedziba dla aktualnego nadleśnictwa
                    expr = f"\"{CONFIG['fields']['district_name']}\" = '{nadl}'"
                    req = QgsFeatureRequest().setFilterExpression(expr)
                    feat = next(l_nadl.getFeatures(req), None)
                    siedziba = feat[CONFIG['fields']['district_office']] if feat else ""
                    
                    # 2. Filtrowanie warstwy raportowej
                    l_rap.setSubsetString(f"\"{CONFIG['fields']['district_name']}\" = '{nadl}'")
                    
                    # 3. Przekazywanie zmiennych kontekstowych do raportu
                    QgsExpressionContextUtils.setProjectVariable(project, 'wybrane_nadl', str(nadl))
                    QgsExpressionContextUtils.setProjectVariable(project, 'wybrana_siedziba', str(siedziba))
                    
                    # 4. Budowanie nazwy pliku i eksport
                    safe_n = str(nadl).replace(' ', '_')
                    fn = f"{layout_name.replace(' ', '_')}_{safe_n}_{p['rok']}_{p['polrocze']}.pdf"
                    file_path = os.path.join(dir_p, fn)
                    
                    QgsLayoutExporter.exportToPdf(lo, file_path, QgsLayoutExporter.PdfExportSettings())
                
                # Czyszczenie filtra po zakończeniu pętli
                l_rap.setSubsetString("")
                iface.messageBar().pushMessage("Sukces", f"Wygenerowano raporty dla {len(p['nadlesnictwa'])} jednostek.", level=Qgis.Success)
 
    def save_all_changes(self):
        """Zapisuje zmiany na wszystkich warstwach bazy."""
        targets = [CONFIG['layers'][k] for k in ['parcels','usages','exclusions','inspections']]
        for name in targets:
            ls = QgsProject.instance().mapLayersByName(name)
            if ls and ls[0].isEditable():
                ls[0].commitChanges()
        iface.messageBar().pushMessage("Baza", "Zapisano zmiany.", level=Qgis.Success)
        self.fetch_manual_data()

    def search_parcel(self):
        """Wyszukuje działkę po TERYT lub Znaku i odświeża widok."""
        txt = self.in_search.text().strip()
        if not txt:
            return
        lyr = QgsProject.instance().mapLayersByName(CONFIG['layers']['parcels'])[0]
        expr = f"\"teryt\"='{txt}' OR \"znak_sprawy\"='{txt}'"
        dz = next(lyr.getFeatures(QgsFeatureRequest().setFilterExpression(expr)), None)
        if dz:
            lyr.selectByIds([dz.id()])
            iface.mapCanvas().setExtent(dz.geometry().boundingBox())
            self.fetch_manual_data()

    def edit_selected_item(self):
        """Otwiera formularz edycji dla zaznaczonego elementu w drzewie."""
        it = self.tree.currentItem()
        if it:
            d = it.data(0, Qt.UserRole)
            lyr = QgsProject.instance().mapLayersByName(d['layer'])[0]
            if not lyr.isEditable():
                lyr.startEditing()
            iface.openFeatureForm(lyr, lyr.getFeature(d['id']))

    def flash_selected_feature(self, it, col):
        """Błyska obiektem na mapie po kliknięciu w drzewie."""
        d = it.data(0, Qt.UserRole)
        if d and d['layer'] != CONFIG['layers']['inspections']:
            lyr = QgsProject.instance().mapLayersByName(d['layer'])[0]
            iface.mapCanvas().flashFeatureIds(lyr, [d['id']])


# --- FUNKCJE STARTOWE ---

def openProject():
    global attr_dock, area_hud
    attr_dock = QDockWidget("Zarządzanie wyłączeniami z produkcji leśnej", iface.mainWindow())
    attr_dock.setWidget(PanelWylaczenia())
    iface.mainWindow().addDockWidget(Qt.RightDockWidgetArea, attr_dock)
    area_hud = SmartAreaHUD(iface.mapCanvas())


def closeProject():
    global attr_dock, area_hud
    if area_hud:
        area_hud.stop()
        area_hud = None
    if attr_dock:
        iface.mainWindow().removeDockWidget(attr_dock)
        attr_dock = None
