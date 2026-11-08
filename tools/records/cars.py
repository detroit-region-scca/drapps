"""Year, make and model from a car as a results sheet prints it ("04 Mazda RX-8", "Chevy Cobalt SS", iRacing's "P9922").
The printed text stays as it is; these are extra columns for searching and counting. Karts and Formula SAE cars have no road
make and keep the text only."""
import re
MAKES = {  # spelling on the sheets -> one name
 'acura':'Acura','alfa romeo':'Alfa Romeo','alfa':'Alfa Romeo','amc':'AMC','aston martin':'Aston Martin','audi':'Audi','austin healey':'Austin-Healey',
 'austin-healey':'Austin-Healey','bmw':'BMW','buick':'Buick','cadillac':'Cadillac','caterham':'Caterham','chevrolet':'Chevrolet','chevy':'Chevrolet','chev':'Chevrolet',
 'chrysler':'Chrysler','datsun':'Datsun','dodge':'Dodge','eagle':'Eagle','ferrari':'Ferrari','fiat':'Fiat','ford':'Ford','genesis':'Genesis','geo':'Geo','gmc':'GMC',
 'honda':'Honda','hyundai':'Hyundai','infiniti':'Infiniti','jaguar':'Jaguar','jeep':'Jeep','kia':'Kia','lamborghini':'Lamborghini','lexus':'Lexus','lincoln':'Lincoln',
 'lotus':'Lotus','mazda':'Mazda','mclaren':'McLaren','mercedes-benz':'Mercedes-Benz','mercedes benz':'Mercedes-Benz','mercedes':'Mercedes-Benz','mercury':'Mercury',
 'mg':'MG','mini':'Mini','mitsubishi':'Mitsubishi','nissan':'Nissan','oldsmobile':'Oldsmobile','olds':'Oldsmobile','plymouth':'Plymouth','pontiac':'Pontiac',
 'porsche':'Porsche','ram':'Ram','saab':'Saab','saturn':'Saturn','scion':'Scion','shelby':'Shelby','smart':'Smart','subaru':'Subaru','suzuki':'Suzuki',
 'tesla':'Tesla','toyota':'Toyota','triumph':'Triumph','volkswagen':'Volkswagen','volkswagon':'Volkswagen','vw':'Volkswagen','volvo':'Volvo','polestar':'Polestar',
 'rivian':'Rivian','lucid':'Lucid','hummer':'Hummer','isuzu':'Isuzu','sunbeam':'Sunbeam','opel':'Opel','renault':'Renault','peugeot':'Peugeot','spec racer':'Spec Racer',
 'radical':'Radical','van diemen':'Van Diemen','swift':'Swift','reynard':'Reynard','tatuus':'Tatuus','ligier':'Ligier','ariel':'Ariel','factory five':'Factory Five'}
# a model alone names its make ("Miata", "Corvette", "WRX")
MODEL_MAKE = {'miata':'Mazda','mx-5':'Mazda','mx5':'Mazda','rx-7':'Mazda','rx7':'Mazda','rx-8':'Mazda','rx8':'Mazda','corvette':'Chevrolet','camaro':'Chevrolet',
 'mustang':'Ford','wrx':'Subaru','sti':'Subaru','brz':'Subaru','civic':'Honda','s2000':'Honda','integra':'Acura','gti':'Volkswagen','golf':'Volkswagen',
 'jetta':'Volkswagen','boxster':'Porsche','cayman':'Porsche','911':'Porsche','neon':'Dodge','focus':'Ford','fiesta':'Ford','gr86':'Toyota','supra':'Toyota',
 'mr2':'Toyota','celica':'Toyota','evo':'Mitsubishi','lancer':'Mitsubishi','350z':'Nissan','370z':'Nissan','240sx':'Nissan','frs':'Scion','fr-s':'Scion','solstice':'Pontiac'}
# iRacing's car codes
SIM = {'p9922': ('Porsche', '911 GT3 Cup (992)'), 'p718': ('Porsche', '718 Cayman GT4'), 'gr22': ('Toyota', 'GR86'), 'mgt4': ('Mercedes-AMG', 'GT4'),
       'dil15': ('Dallara', 'IL-15'), 'm570s': ('McLaren', '570S GT4'), 'amv4': ('Aston Martin', 'Vantage GT4'), 'bmwgt4': ('BMW', 'M4 GT4'),
       'srf': ('Spec Racer', 'Ford'), 'mx5': ('Mazda', 'MX-5 Cup'), 'ff1600': ('Ray', 'FF1600'), 'f4': ('Dallara', 'F4')}
MAKES['mazdaspeed'] = 'Mazda'
KEYS = sorted(MAKES, key=len, reverse=True)
MODEL_FIX = [(r'(?i)^(mx-?5 miata|miata mx-?5|mx-?5|miata)\b', 'MX-5 Miata'), (r'(?i)^rx-?8\b', 'RX-8'), (r'(?i)^rx-?7\b', 'RX-7'),
             (r'(?i)^s-?2000\b', 'S2000'), (r'(?i)^(mazda)?speed ?3\b', 'Mazdaspeed3'), (r'(?i)^gr ?86\b', 'GR86'), (r'(?i)^fr-?s\b', 'FR-S'),
             (r'(?i)^wrx sti\b', 'WRX STI'), (r'(?i)^c(\d)\b', r'C\1')]


def tidy_model(m):
    """One spelling for common models ("RX8", "Rx-8" -> "RX-8"; "Miata", "MX-5" -> "MX-5 Miata"); codes with digits upper case."""
    if not m:
        return m
    for rx, to in MODEL_FIX:
        m = re.sub(rx, to, m)
    return ' '.join(w.upper() if re.search(r'\d', w) and len(w) <= 6 and w.isalnum() else w for w in m.split())


def parse(v):
    """'04 Mazda RX-8' -> (2004, 'Mazda', 'RX-8'); a car that does not split -> (year or None, None, None)."""
    if not v: return (None, None, None)
    t = ' '.join(v.replace('_', ' ').split())
    if t.lower() in SIM: return (None,) + SIM[t.lower()]
    year = None
    m = re.match(r"^'?(\d{4}|\d{2})\b[\s.-]*", t)
    if m:
        y = int(m[1]); y = y if y > 1900 else (2000 + y if y <= 30 else 1900 + y)
        if 1900 < y <= 2030: year, t = y, t[m.end():]
    low = t.lower()
    for k in KEYS:
        if low == k or low.startswith(k + ' ') or low.startswith(k + '-') or low.startswith(k + '/'):
            model = (('Mazdaspeed ' if k == 'mazdaspeed' else '') + t[len(k):].strip(' -/')).strip() or None
            m2 = re.search(r'\s+(\d{4})$', model or '')            # "Miata 1999"
            if m2 and not year and 1900 < int(m2[1]) <= 2030: year, model = int(m2[1]), model[:m2.start()].strip() or None
            return (year, MAKES[k], tidy_model(model))
    first = low.split(' ')[0] if low else ''
    if first in MODEL_MAKE:
        m2 = re.search(r'\s+(\d{4})$', t)                       # "Miata 1999"
        if m2 and not year and 1900 < int(m2[1]) <= 2030:
            year, t = int(m2[1]), t[:m2.start()].strip()
        return (year, MODEL_MAKE[first], tidy_model(t))
    return (year, None, None)
