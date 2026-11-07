# Wymagania dla firmware loggera · LapTune

Stan aplikacji: implementacja EST/ESKF v2, układ pojazdu FLU. Sprzęt: LSM6DS3 oraz u-blox NEO-6.
Dokument określa wymagania projektu; konkretne warianty układów, piny i możliwości ich firmware
programista ma potwierdzić na urządzeniu. Kalibracja użytkowa odbywa się podczas jazdy.

## 1. Cel i kolejność prac

Zapewnić wiarygodne, zsynchronizowane pomiary do kalibracji montażu IMU oraz estymacji pozycji
między epokami GPS i podczas utraty sygnału. Priorytetem jest jakość danych wejściowych.

| Priorytet | Zakres | Efekt |
|---|---|---|
| P0 — obowiązkowy | Monotoniczny czas IMU, rzeczywiste nowe próbki, spójne epoki GPS, poprawne jednostki, brak blokowania akwizycji | Usunięcie obecnego zgadywania czasu i świeżości danych |
| P1 — docelowy | Wspólny czas pomiarów IMU/GPS, PPS, znana latencja, dokładności i prędkości GNSS, temperatura i błędy IMU | Lepsza synchronizacja, kalibracja i dobór zaufania do pomiarów |
| P2 — rozszerzenia | Odometria/CAN, dane do identyfikacji szumów i wpływu temperatury, ewentualna modernizacja GNSS | Ograniczenie dryfu podczas dłuższych zaników GPS |

W logach zaobserwowano zaniki czasu razem z GPS i cofnięcie zegara o około 3 s. Krok ACC wynosi
0,000488, a GYRO 0,070, co odpowiada przelicznikom ±16 g i ±2000 °/s. To wskazówka, a nie dowód
poprawnej konfiguracji rejestrów. Aplikacja obecnie szacuje część czasu i rozpoznaje świeżość GPS
heurystycznie, jeśli brakuje jawnych znaczników.

## 2. Kontrakt zgodny z obecną aplikacją — P0

Zachować nazwy i znaczenie istniejących pól:

`NR;DATA;CZAS;ACC_X;ACC_Y;ACC_Z;GYRO_X;GYRO_Y;GYRO_Z;GPS_LAT;GPS_LON;GPS_SPEED_KMH;GPS_ALT_MSL_M`

Do każdego wiersza dodać poniższe pola. Zalecane nazwy są dokładne, z uwzględnieniem wielkości liter.

| Pole | Typ/jednostka | Wymagane zachowanie |
|---|---|---|
| `T_IMU_US` | uint64, µs | Czas pobrania danej próbki IMU, w niezależnej monotonicznej skali MCU |
| `GPS_STATE` | `N`, `R`, `I` | Nowa poprawna epoka, powtórzenie zachowanego rozwiązania, brak poprawnej pozycji |
| `GPS_FIX_ID` | liczba całkowita | Identyfikator epoki; zmienia się raz na nową epokę, jest stały dla jej powtórzeń |
| `GPS_ITOW_MS` | uint32, ms tygodnia GPS | Zalecany dodatkowo: oryginalny `iTOW` z UBX, bez przeliczania go na czas odbioru |

`GPS_STATE` ma pierwszeństwo w obecnym filtrze. Dostępne są też numeryczne alternatywy `GPS_NEW`
i `GPS_VALID` — 1/0. Jeśli zapisywane są równocześnie flagi i identyfikatory, muszą być spójne.
Obecna aplikacja wybiera `GPS_FIX_ID` przed `GPS_ITOW_MS`, jeśli oba pola występują.

### Czas IMU

- Uruchomić sprzętowy licznik czasu niezależny od GPS, UTC, SD i pętli głównej. Docelowo 64 bity,
  rozdzielczość 1 µs. Licznik 32-bitowy rozszerzyć poprawnie o przepełnienia; przy 1 MHz przepełnia
  się po około 71 min 35 s. Zapisywać liczby całkowite, bez pośredniego `float` 32-bitowego.
- Timestamp przejmować przy DRDY/Input Capture albo z poprawnie odwzorowanego czasu próbek FIFO.
  Podać jego relację do chwili fizycznego pomiaru i opóźnienia toru filtracji. Czas odczytu magistrali
  lub zapisu do SD nie zastępuje czasu próbki.
- Nie nadawać całej paczce FIFO jednego czasu. Rekonstrukcję czasu z ODR oznaczyć i opisać;
  wymaga ona potwierdzonej rzeczywistej częstotliwości i obsługi przepełnień.
- `T_IMU_US` ma rosnąć również przed pierwszym GPS, w tunelu i podczas utraty transmisji GNSS.
  Nie zerować go przy nowym TXT, nowym fixie, zmianie UTC ani synchronizacji PPS.
- `NR` zwiększać dla każdej pozyskanej próbki, również gdy później zostanie zgubiona przez kolejkę.
  Brakujące numery mają ujawniać utratę danych. Nie dodawać duplikatów IMU w celu wypełnienia luk.
- Reset MCU rozpoczyna nową sesję z nowym identyfikatorem uruchomienia w metadanych.
  Każdą sesję/uruchomienie zapisywać w osobnym folderze z własnym `SESSION.json` i zestawem TXT.
  Użytkownik wybiera w aplikacji folder konkretnej sesji; zmiana mocowania IMU również rozpoczyna
  nową sesję, ponieważ kalibracja zakłada stały obrót montażu w analizowanym zapisie.

Filtr potrzebuje rzeczywistego `dt = (T_IMU_US[k] - T_IMU_US[k-1]) / 1e6`.
Nie wymuszać w logu idealnego dt, jeśli urządzenie rzeczywiście próbkowało inaczej.

### Świeżość i ważność GPS

- `N`: jedna nowa, poprawna epoka rozwiązania nawigacyjnego. Oznaczenie wynika z czasu/ID epoki,
  nie z porównania współrzędnych. Nowy pomiar może mieć identyczne wartości jak poprzedni.
- `R`: dokładna kopia poprzedniej opublikowanej epoki, z tym samym ID i `iTOW`.
  Nie dopisywać litery R do pól liczbowych.
- `I`: brak poprawnej pozycji. Przed pierwszym fixem i podczas utraty GPS pozostawić puste pola
  pozycji, prędkości i wysokości; nie wpisywać zera i nie odświeżać starego fixa jako nowego.
- Brak wiadomości odbiornika ma być wykrywany przez jawny timeout, np. 3 brakujące epoki plus
  uzasadniony margines transmisji. Zapisać przyjęty próg. Dla 5 Hz typowy punkt wyjścia to 0,6–1 s.
- Należy rozróżniać checksum error, utratę UART i rozwiązanie GNSS bez poprawnego fixa.

Komunikaty pozycji, prędkości, wysokości i statusu składać według tego samego `iTOW` lub spójnego
czasu UTC. Nowsza GGA nie może aktualizować wysokości w pozycji ze starszej RMC. `N` publikować
raz po złożeniu epoki. Ustalić deadline składania i sposób raportowania brakujących wiadomości.
Jeśli epoka ma poprawną pozycję 2D, ale nie ma poprawnej wysokości, pozostawić wysokość pustą.
Brakująca prędkość również pozostaje pusta. Późny fragment nie tworzy drugiej poziomej korekty
tej samej epoki. Pełne pakiety można dodatkowo archiwizować do diagnostyki.

## 3. Synchronizacja czasu pomiaru GPS — P1

Zapisać osobno:

| Pole | Znaczenie |
|---|---|
| `GPS_T_MEAS_US` | Czas epoki GPS odwzorowany na tę samą skalę MCU co `T_IMU_US` |
| `GPS_T_RX_US` | Czas odebrania ostatniego komunikatu potrzebnego do złożenia tej epoki |
| `GPS_LATENCY_US` | `GPS_T_RX_US - GPS_T_MEAS_US`, z określoną semantyką odbioru |
| `TIME_SYNC_STATE` | Kod: 0 brak wiarygodnej synchronizacji, 1 PPS locked, 2 holdover |
| `TIME_SYNC_ERROR_US` | Oszacowanie błędu mapowania zegarów; nie sztucznie stałe zero |

Preferowane: TIMEPULSE/PPS NEO-6 podłączone do Input Capture MCU i poprawne powiązanie impulsów
z czasem odbiornika oraz flagami ważności czasu. Mapowanie można opisać jako
`t_MCU = a * t_GPS + b`, gdzie a uwzględnia różnicę częstotliwości zegarów. Podczas zaniku GPS
kontynuować zegar MCU i oznaczyć holdover. Nie korygować skokowo monotonicznego licznika.
Obsłużyć granicę tygodnia GPS, północ, ważność daty/czasu i różnicę skal GPS/UTC; nie kodować
na stałe przesunięcia GPS–UTC w ogólnym mechanizmie synchronizacji.

Jeśli PPS nie jest doprowadzone, programista ma opisać ograniczenie i udokumentować alternatywne
odwzorowanie czasu oraz jego błąd. Czas nadejścia UART nie jest dokładnym czasem rozwiązania GNSS.
Przy 30 m/s błąd przypisania czasu 100 ms oznacza około 3 m różnicy położenia.

**Granica obecnej aplikacji:** nowe kolumny czasu GPS są dziś archiwizowane jako liczby, ale nie są
wykorzystywane do opóźnionych korekt ESKF. Obecny filtr przypisuje GPS do czasu wiersza IMU.
Samo dodanie kolumn nie usuwa tej latencji.

Dwie poprawne ścieżki integracji:

1. **Zgodność z obecną aplikacją:** buforować w RAM chronologiczne wiersze IMU, zaczekać na epokę GPS
   i przypisać `N` do najbliższego rzeczywistego czasu próbki. Dla 208 Hz błąd samego dopasowania
   do najbliższej próbki nie przekracza około 2,4 ms. Nie zmieniać timestampu IMU na czas GPS.
   Wiersze `R` po tej epoce mają powielać właściwy cache. Log zapisuje uporządkowaną historię;
   zapis do SD jest opóźniony o pojemność bufora. Późne epoki poza buforem raportować.
2. **Docelowa architektura:** zapisywać asynchroniczne pomiary z ich czasami i czasami odbioru,
   a w aplikacji dodać bufor stanów, korektę w chwili pomiaru i ponowną propagację IMU.
   Wymaga to uzgodnienia formatu i zmian aplikacji. Osobne pliki GNSS/eventów zapisywać jako
   `.csv`, `.jsonl` lub `.ubx`; obecny importer folderu ich automatycznie nie scala.

Rozmiar buforów dobrać z pomiarów maksymalnego opóźnienia GNSS i najgorszych pauz SD, z zapasem.
Nie poprawiać pozornie czasu przez wpisywanie epoki GPS do bieżącego wiersza odebranego 100–300 ms później.

## 4. LSM6DS3: konfiguracja, jednostki i integralność

- Potwierdzić dokładny wariant, WHO_AM_I, wersję sterownika i odczytać konfigurację po inicjalizacji.
  LSM6DS3 i pokrewne warianty nie mogą być traktowane jako identyczne bez sprawdzenia dokumentacji.
- Punkt wyjścia: ACC i GYRO **208 Hz**; alternatywnie 104 Hz, jeśli uzasadniają to zasoby.
  Zapisywać rzeczywistą częstotliwość pomiarów. Nie uzyskiwać pozornych 100 Hz przez losowe odczyty
  czujnika 104/208 Hz z pętli `delay(10)` i nie powielać jego nieodświeżonych rejestrów.
- Użyć DRDY/FIFO, BDU, autoinkrementacji i spójnego odczytu wektorów. Sprawdzić signed int16,
  kolejność bajtów oraz świeżość osobno dla ACC i GYRO. BDU samo nie dowodzi ich synchronizacji.
  Dla wspólnego wiersza sześciu osi zweryfikować przesunięcie czasów obu pomiarów. Przy różnych
  czasach/ODR logować je osobno i uzgodnić obsługę w aplikacji.
- Sterownik FIFO ma odpowiadać konkretnemu układowi — nie zakładać tagów FIFO z nowszej rodziny.
  Raportować overrun i utracone próbki; nie odtwarzać brakujących wartości przez powtarzanie cache.
- Zakresy dobrać po pomiarze szczytów i clippingu. Kandydat do testów: **±8 g i ±500 °/s**.
  To nie jest nakaz obniżenia zakresów w ciemno. W razie uderzeń lub szybkich obrotów zachować
  większy zakres. Konfiguracja stała w sesji, z udokumentowanym zapasem do nasycenia.

Przeliczniki dla standardowego LSM6DS3, do weryfikacji z faktycznym odczytem konfiguracji:

| Zakres | Współczynnik dla signed ADC |
|---|---|
| ACC ±4 g | `ACC_g = raw * 0.000122` |
| ACC ±8 g | `ACC_g = raw * 0.000244` |
| ACC ±16 g | `ACC_g = raw * 0.000488` |
| GYRO ±500 °/s | `GYRO_dps = raw * 0.0175` |
| GYRO ±2000 °/s | `GYRO_dps = raw * 0.070` |

Zachować ACC w g z grawitacją oraz GYRO w °/s. Do m/s² przeliczamy przez 9,80665.
Nie normalizować wektora ACC do 1 g, nie usuwać średniej, nie wykonywać niejawnego zerowania przy
starcie i nie obracać danych do układu pojazdu. Autozerowanie podczas ruchu usunęłoby rzeczywisty
sygnał. Logować osie czujnika, opisać ich orientację względem PCB; układ docelowy aplikacji to
X do przodu, Y w lewo, Z do góry. Kalibracja montażu i biasu pozostaje po stronie aplikacji.

Filtrację antyaliasingową dobrać przed ewentualną decymacją. Dla 208 Hz rozważyć użyteczne pasmo
około 20–40 Hz, wyłącznie przy ustawieniach rzeczywiście wspieranych przez układ. Podać wszystkie
włączone filtry, pasmo, ODR i opóźnienie grupowe osobno dla ACC/GYRO. Sama filtracja w aplikacji
nie usuwa aliasingu, który już powstał podczas akwizycji.

Zalecane dodatkowe dane: surowe signed ADC, temperatura `IMU_TEMP_C`, flagi clippingu, błędów
magistrali i utraty synchronizacji. W obecnej aplikacji flagi `IMU_VALID`/clipping nie maskują
automatycznie pomiarów. Błędy transportu muszą dawać puste niewiarygodne wektory; oryginalne
odczyty diagnostyczne można zachować osobno. Wykorzystanie flag nasycenia w ESKF wymaga adaptera
aplikacji. Nie zmieniać po cichu liczb na zero ani nie traktować nasycenia jako poprawnego ruchu.

## 5. u-blox NEO-6: epoki i jednostki

- Odczytać MON-VER, potwierdzić wariant modułu, możliwości i stan konfiguracji. Punkt wyjścia:
  rozwiązanie 5 Hz, `CFG-RATE measRate=200 ms`, `navRate=1`; potwierdzić ACK, odczyt ustawień
  i rzeczywistą różnicę `iTOW`. Nie zakładać 10/20 Hz ani komunikatów nowszych generacji.
- Preferować UBX: `NAV-POSLLH`, `NAV-VELNED`, `NAV-SOL` lub zgodny status `NAV-STATUS`.
  Wszystkie pola danej epoki muszą dotyczyć tego samego `iTOW`. Nie zakładać dostępności NAV-PVT
  w NEO-6. Status powinien uwzględniać poprawny fix i flagę `gpsFixOK` oraz ważność czasu.
- Zweryfikować model dynamiki samochodu, np. `CFG-NAV5 automotive`, i ustawienia trybu oszczędzania
  energii/static hold. Potwierdzić ich dostępność i udokumentować rzeczywiste wartości.
- UART z DMA/buforem; rozważyć 38400–115200 bit/s zgodnie z możliwościami modułu. Przy 8N1 jeden
  bajt zajmuje 10 bitów. Budżet transmisji obliczyć dla wszystkich włączonych komunikatów z zapasem;
  wyłączyć niepotrzebne duplikaty NMEA/UBX, zamiast przepełniać UART przy 9600 bit/s.
- Weryfikować checksumy. Bufory odbiorcze muszą przetrwać bursty i pauzy SD.

| Pole docelowe | Przeliczenie ze starszych komunikatów UBX |
|---|---|
| `GPS_LAT`, `GPS_LON` [deg] | signed `lat/lon * 1e-7` |
| `GPS_ALT_MSL_M` [m] | `hMSL * 0.001`; nie używać elipsoidalnego `height` pod tą nazwą |
| `GPS_SPEED_KMH` [km/h] | `gSpeed * 0.036`; gSpeed jest poziome i podane w cm/s |
| `GPS_VEL_N_M_S`, `GPS_VEL_E_M_S` | `velN/velE * 0.01` |
| `GPS_VEL_U_M_S` | `-velD * 0.01`, ponieważ UBX używa dodatniego kierunku w dół |
| `GPS_HACC_M`, `GPS_VACC_M` | `hAcc/vAcc * 0.001` |
| `GPS_SPEED_ACC_M_S` | `sAcc * 0.01` z NAV-VELNED |
| `GPS_COURSE_DEG`, `GPS_COURSE_ACC_DEG` | `heading/cAcc * 1e-5` |

Nie mylić `gSpeed` z prędkością 3D `speed`. Jeśli używane jest NMEA, prędkość RMC w węzłach
mnożymy przez 1,852, a współrzędne stopnie/minuty prawidłowo zamieniamy na stopnie dziesiętne.
Logować kurs/prędkość odbiornika, zamiast zastępować je własną różnicą kolejnych pozycji.
Kurs ruchu nie jest orientacją nadwozia, zwłaszcza przy postoju, cofaniu i poślizgu.
Współrzędne przechowywać jako oryginalne int32 lub przeliczać przez double. Float32 przy wartościach
kilkudziesięciu stopni może stracić dziesiątki centymetrów; wypisanie wielu miejsc po przecinku
nie odtworzy utraconych bitów.

Dodać status fixa, liczbę satelitów i DOP. **HDOP nie jest błędem w metrach.** Estymaty hAcc/vAcc
również trzeba opisać zgodnie z protokołem, bez arbitralnego nazywania ich gwarantowanym 1σ.
Nie wygładzać dodatkowo pozycji własnym filtrem przed logowaniem. Zachować dane źródłowe.

## 6. Akwizycja, zapis i metadane

Oddzielić akwizycję/znaczniki czasu od formatowania tekstu i SD. ISR przejmuje czas i dane/status,
a zapis działa z kolejki. Nie wykonywać `printf`, zapisu SD ani oczekiwania na GPS w krytycznej
ścieżce próbkowania. Przy wspólnej magistrali sprawdzić blokowanie IMU przez sterownik karty.

Zapisywać liczniki: utracone próbki, FIFO overrun, błędy IMU, UART overrun/checksum, niekompletne
epoki GPS, pełną kolejkę SD i maksymalną pauzę zapisu. Przepełnienia muszą być widoczne w danych.

Format TXT: UTF-8/ASCII, separator `;`, kropka dziesiętna, stała liczba pól, nagłówek w pierwszym
wierszu każdego pliku. Brak pomiaru to puste pole. Zalecana precyzja: ACC g 6 miejsc, GYRO °/s
6 miejsc, pozycja 7–8 miejsc, wysokość m 3 miejsca. Precyzja zapisu nie jest deklaracją dokładności.
`NR` i `T_IMU_US` zapisywać jako całkowite liczby dziesiętne. `DATA`: `YYYY-MM-DD`;
`CZAS`: `HH:MM:SS.ffffff` (obsługiwane są także milisekundy). Informację o UTC przechowywać
w metadanych; do istniejącego pola CZAS nie dopisywać `Z` ani strefy, których obecny parser nie obsługuje.

Rotacja `LOGxxxx.TXT` nie resetuje czasu, NR ani ID epoki. Numeracja plików ma odzwierciedlać
kolejność akwizycji. Nie dodawać wierszy komentarzy przed nagłówkiem ani swobodnego tekstu w tabeli.
Obecny importer specjalnie rozpoznaje tekstowe `GPS_STATE`; pozostałe dodatkowe kolumny są liczbowe.

Metadane zapisać do `SESSION.json`, **nie do dodatkowego TXT**: aplikacja próbuje wczytać każdy TXT
w wybranym folderze jako log. Metadane powinny zawierać wersję formatu i firmware/commit, identyfikator
sesji/uruchomienia, modele MCU/IMU/GPS, WHO_AM_I/MON-VER, odczyty rejestrów, zakresy i współczynniki,
ODR/filtry/opóźnienia, źródło czasu i jego jednostki, konfigurację UART/GNSS, schemat flag, timeouty,
orientację osi oraz położenie anteny względem IMU. Obecna aplikacja nie importuje tego JSON automatycznie.

DATA/CZAS pozostają informacją dodatkową. Jeśli odpowiadają UTC, opisać to jawnie; mogą być puste
przed ustaleniem czasu. Nie zastępować UTC licznikiem od startu. Holdover UTC po utracie GPS wymaga
oznaczenia jakości czasu. Podstawą integracji pozostaje T_IMU_US.

Nie dodawać własnych kolumn `EST_…` do surowego logu — są zarezerwowane dla wyników aplikacji.

## 7. Kryteria odbioru

Poniższe wartości czasowe są celami projektu do potwierdzenia pomiarem, nie parametrami gwarantowanymi
przez same układy scalone. Raport ma zawierać metodę, percentyle i najgorszy wynik.

| Test | Wymagany wynik |
|---|---|
| Ciągłość zegara | T_IMU_US rośnie bez resetów przez co najmniej 2 h, granice plików i utratę GPS; poprawne przepełnienie licznika sprzętowego |
| Dokładność timestampu IMU | Cel p99 błędu względem zdarzenia DRDY ≤100 µs; stały offset i opóźnienie filtracji podane osobno |
| Parowanie sześciu osi | Cel przesunięcia ACC/GYRO ≤0,5 ms; inaczej oddzielne czasy i jawna obsługa w aplikacji |
| Synchronizacja GPS/IMU | Z PPS cel p99 łącznego błędu przypisania czasów ≤5 ms; osobno zmierzyć mapowanie zegarów, dopasowanie do próbki i opóźnienia filtrów; bez PPS podać zmierzony błąd i ograniczenia |
| Częstotliwość | Rzeczywisty ODR zmierzony, zgodny z tolerancją czujnika; nowe epoki GPS przy 5 Hz co około 200 ms, brak sztucznych próbek |
| Obciążenie SD/UART | Co najmniej 30 min z maksymalnym ruchem i rotacją plików bez utraty próbek w warunkach nominalnych |
| Kontrolowane przeciążenie | Wymuszone przepełnienie daje liczniki/flagę/lukę, a nie fałszywie ciągły log |
| Powtórzenia GPS | R ma ten sam ID; nowa identyczna pozycja dostaje N i nowy ID; jedna korekta na epokę |
| Zanik GPS 5/10/20 s | IMU i czas działają dalej; brak fałszywych N i zer pozycji; poprawna identyfikacja powrotu GPS |
| Błędy ramek | Uszkodzony checksum lub niepełna epoka nie tworzy poprawnego nowego pomiaru |
| Konfiguracja/jednostki | Odczyty rejestrów zgodne z przelicznikami; podpisane osie i kierunki potwierdzone niezależnym testem |
| Import | Wszystkie TXT w folderze wczytują się w LapTune; surowe nazwy i jednostki zachowane; bez szacowania czasu tam, gdzie dostarczono poprawny T_IMU_US |

Do odbioru przekazać: firmware z identyfikatorem wersji, dump konfiguracji, opis pinów DRDY/PPS,
surowe logi z testów, metadane oraz raport czasów i utraty danych. Diagnostyka przy nieruchomym
module na stole jest dopuszczalnym testem laboratoryjnym, nie wymaganą procedurą kalibracji pojazdu.
Nie uruchamiać automatycznego zerowania przy starcie auta.

Test nawigacyjny: osobny fragment jazdy do kalibracji, następnie inny do walidacji, z prostymi,
przyspieszaniem, hamowaniem, zakrętami i zmianami nachylenia. Wykonać rzeczywiste i symulowane zaniki.
Dokładność pozycji oceniać względem niezależnej referencji, np. zweryfikowanego RTK. Porównanie z GPS,
który zasila filtr, nie dowodzi poprawy dokładności absolutnej. Usunięte fragmenty tego GPS dają
kontrolę zgodności, ale nie zastępują niezależnego wzorca. Nie gwarantować z góry metrów błędu w tunelu.

## 8. Co wymaga współpracy z aplikacją

| Dane/funkcja | Stan w obecnym LapTune |
|---|---|
| T_IMU_US, GPS_STATE, GPS_FIX_ID/GPS_ITOW_MS, GPS_NEW/GPS_VALID | Wykorzystywane przez EST |
| Spójne GPS przypisane do właściwego wiersza IMU | Wykorzystywane, przy założeniu wspólnego czasu pomiaru |
| GPS_T_MEAS_US/GPS_T_RX_US i opóźnione obserwacje | Potrzebna obsługa czasu pomiaru i bufora stanów w aplikacji |
| hAcc/vAcc/sAcc, kurs i składowe prędkości GNSS | Mogą być zapisane jako dodatkowe liczby; filtr nie korzysta z nich automatycznie |
| Temperatura, IMU_VALID, clipping i diagnostyka | Archiwizacja; potrzebne mapowanie do modelu/ważności i raportu aplikacji |
| SESSION.json, oddzielne strumienie GNSS/CAN | Potrzebny importer/metadane/synchronizacja strumieni |
| Prędkość kół, bieg, kąt kierownicy | Warto logować z czasem MCU; fuzja z ESKF to osobny etap |

Dla dłuższych zaników najbardziej wartościowym rozszerzeniem może być prędkość kół/odometria.
Sam LSM6DS3 dryfuje wskutek błędów biasu, temperatury i orientacji. NEO-6 nie uzyska dokładności RTK
przez samą zmianę firmware loggera. Utrzymać sztywne mocowanie IMU i dobrą instalację anteny GPS,
ograniczyć zakłócenia i opisać odległość antena–IMU; obecny filtr zakłada ich współlokację.

## Referencje do weryfikacji implementacji

- [Oficjalne przeliczniki ST LSM6DS3](https://github.com/STMicroelectronics/lsm6ds3-pid/blob/master/lsm6ds3_reg.c).
- [Definicje konfiguracji ST LSM6DS3](https://github.com/STMicroelectronics/lsm6ds3-pid/blob/master/lsm6ds3_reg.h).
- [NAV-POSLLH: definicje i jednostki w sterowniku ROS](https://github.com/KumarRobotics/ublox/blob/master/ublox_msgs/msg/NavPOSLLH.msg).
- [NAV-VELNED: definicje i jednostki w sterowniku ROS](https://github.com/KumarRobotics/ublox/blob/master/ublox_msgs/msg/NavVELNED.msg).
- [CFG-RATE](https://github.com/KumarRobotics/ublox/blob/master/ublox_msgs/msg/CfgRATE.msg).
- Dokumentacja protokołu i datasheet u-blox właściwe dla wariantu/wersji odczytanej z urządzenia
  mają pierwszeństwo przed przykładami bibliotek. Schematy nowszych odbiorników zawierają pola
  niedostępne w NEO-6.
- [Obecny algorytm i ograniczenia LapTune](navigation.md).
