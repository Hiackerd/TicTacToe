from flask import Flask, render_template, request, redirect, url_for, jsonify
import uuid
import time
import hashlib
from threading import Lock

app = Flask(__name__)
app.config['SECRET_KEY'] = 'geheimes_key_2024'

# Alle Lobbys
lobbies = {}
# Thread-sichere Lock
chat_lock = Lock()

# Bann-Wortliste (mit Varianten)
BANNED_WORDS = [
    # Rassistische/Nationalsozialistische Begriffe
    'nazi', 'hitler', 'heil hitler', 'faschist', 'terrorist',
    'judensau', 'kanake', 'nigger', 'nigga', 'faggot', 'schwuchtel',
    'spasti', 'krüppel',
    
    # Gewaltaufrufe
    'kill', 'töten', 'umbringen', 'ich bring dich um', 'stirb',
    'verrecke', 'mord', 'massacre', 'slaughter',
    
    # Beleidigungen
    'hurensohn', 'arsch', 'idiot', 'halt die fresse', 'verpiss dich',
    'geh sterben', 'niemand mag dich', 'du bist nichts',
    'lern erstmal', 'uninstall', 'kys',
    
    # Schimpfwörter mit Varianten
    'fuck', 'f.ck', 'f u c k', 'f@ck', 'shit', 'sh.t', 'bitch', 'b.tch',
    'ass', 'a$$',
]

# Wortvarianten generieren
WORD_VARIANTS = {}
for word in BANNED_WORDS:
    variants = set()
    word_lower = word.lower()
    variants.add(word_lower)
    variants.add(word_lower.replace(' ', ''))
    variants.add(word_lower.replace('a', '@').replace('i', '1').replace('e', '3').replace('o', '0'))
    variants.add(word_lower.replace('s', '$').replace('a', '4'))
    for variant in list(variants):
        variants.add(variant.replace('l', '1').replace('i', '1').replace('e', '3'))
    WORD_VARIANTS[word_lower] = variants

def contains_banned_words(text):
    """Prüft ob Text gebannte Wörter enthält"""
    if not text:
        return None
    
    text_lower = text.lower()
    
    # Einfache Zeichen ersetzen für bessere Erkennung
    text_clean = text_lower
    replacements = {'@': 'a', '1': 'i', '3': 'e', '0': 'o', '$': 's', '4': 'a'}
    for old, new in replacements.items():
        text_clean = text_clean.replace(old, new)
    
    # Nach allen Varianten suchen
    for word, variants in WORD_VARIANTS.items():
        for variant in variants:
            if variant in text_lower or variant in text_clean:
                return word
    return None

def sanitize_message(message):
    """Ersetzt gebannte Wörter mit ***"""
    if not message:
        return message
    
    words = message.split()
    cleaned_words = []
    
    for word in words:
        original_word = word
        word_lower = word.lower()
        
        # Zeichen ersetzen für Prüfung
        test_word = word_lower
        replacements = {'@': 'a', '1': 'i', '3': 'e', '0': 'o', '$': 's', '4': 'a'}
        for old, new in replacements.items():
            test_word = test_word.replace(old, new)
        
        banned = False
        for banned_word, variants in WORD_VARIANTS.items():
            for variant in variants:
                if variant in word_lower or variant in test_word:
                    # Ersetze das Wort mit Sternchen
                    cleaned_words.append('*' * len(original_word))
                    banned = True
                    break
            if banned:
                break
        
        if not banned:
            cleaned_words.append(original_word)
    
    return ' '.join(cleaned_words)

def get_player_hash(player_name, ip_address=None):
    """Erstellt einen Hash für Spieler-Identifikation"""
    if ip_address:
        return hashlib.md5(f"{player_name}_{ip_address}".encode()).hexdigest()
    return hashlib.md5(player_name.encode()).hexdigest()

# ---------------------------------
# ROUTES
# ---------------------------------
@app.route('/')
def index():
    # Nur öffentliche Lobbys, die noch nicht voll sind
    public_lobbies = []
    current_time = time.time()
    
    # Alte Lobbys aufräumen (älter als 1 Stunde)
    expired_lobbies = []
    for room_id, lobby in lobbies.items():
        if current_time - lobby.get('created_at', 0) > 3600:
            expired_lobbies.append(room_id)
    
    for room_id in expired_lobbies:
        if room_id in lobbies:
            del lobbies[room_id]
    
    for lobby in lobbies.values():
        if lobby['type'] == 'public' and not lobby.get('winner'):
            if len(lobby.get('players', [])) < 2:
                public_lobbies.append(lobby)
    
    return render_template('index.html', lobbies=public_lobbies)

@app.route('/create', methods=['POST'])
def create_lobby():
    player_name = request.form.get('player_name', 'Spieler')
    lobby_name = request.form.get('lobby_name', 'Lobby')
    ltype = request.form.get('type', 'public')
    
    # Namen validieren
    if not player_name.strip():
        return render_template('error.html', message="Bitte gib einen Namen ein.")
    
    room_id = str(uuid.uuid4())[:8]
    code = str(uuid.uuid4())[:6].upper() if ltype == 'private' else None

    # Erster Spieler (X) wird sofort hinzugefügt
    player = {
        'name': player_name.strip(), 
        'symbol': 'X',
        'hash': get_player_hash(player_name.strip(), request.remote_addr),
        'ip': request.remote_addr
    }
    
    lobbies[room_id] = {
        'id': room_id,
        'name': lobby_name.strip(),
        'type': ltype,
        'code': code,
        'players': [player],
        'board': [""]*9,
        'turn': None,
        'winner': None,
        'started': False,
        'created_at': time.time(),
        'chat': []
    }
    
    # Weiterleitung zur Lobby mit Spielernamen
    return redirect(f'/lobby/{room_id}?player={player_name}')

@app.route('/join/<room_id>', methods=['POST'])
def join_public_lobby(room_id):
    player_name = request.form.get('player_name', 'Spieler').strip()
    
    if not player_name:
        return render_template('error.html', 
                             message="Bitte gib einen Namen ein."), 400
    
    lobby = lobbies.get(room_id)
    
    if not lobby:
        return render_template('error.html', 
                             message="Lobby existiert nicht."), 404
    
    # Prüfen ob Platz noch frei ist
    if len(lobby.get('players', [])) >= 2:
        return render_template('error.html', 
                             message="Lobby ist bereits voll."), 400
    
    # Name schon vergeben?
    if any(p['name'] == player_name for p in lobby.get('players', [])):
        return render_template('error.html', 
                             message="Name bereits vergeben."), 400
    
    # Zweiter Spieler (O) hinzufügen
    player = {
        'name': player_name, 
        'symbol': 'O',
        'hash': get_player_hash(player_name, request.remote_addr),
        'ip': request.remote_addr
    }
    lobby['players'].append(player)
    
    # Spiel starten wenn 2 Spieler da sind
    if len(lobby['players']) == 2:
        lobby['started'] = True
        lobby['turn'] = lobby['players'][0]['name']  # X beginnt
    
    return redirect(f'/lobby/{room_id}?player={player_name}')

@app.route('/lobby/<room_id>')
def join_lobby(room_id):
    player_name = request.args.get('player', '')
    lobby = lobbies.get(room_id)
    
    if not lobby:
        return render_template('error.html', 
                             message="Lobby existiert nicht."), 404
    
    # Prüfen ob Spieler in der Lobby ist
    if player_name and not any(p.get('name') == player_name for p in lobby.get('players', [])):
        return render_template('error.html', 
                             message="Spieler nicht in dieser Lobby."), 403
    
    return render_template('lobby_poll.html', 
                          lobby=lobby, 
                          player_name=player_name)

@app.route('/join_by_code', methods=['POST'])
def join_by_code():
    code = request.form.get('code', '').upper().strip()
    player_name = request.form.get('player_name', 'Spieler').strip()
    
    if not player_name:
        return render_template('error.html', 
                             message="Bitte gib einen Namen ein."), 400
    
    for lobby in lobbies.values():
        if lobby.get('code') == code:
            if len(lobby.get('players', [])) >= 2:
                return render_template('error.html', 
                                     message="Lobby ist bereits voll."), 400
            
            # Name schon vergeben?
            if any(p.get('name') == player_name for p in lobby.get('players', [])):
                return render_template('error.html', 
                                     message="Name bereits vergeben."), 400
            
            # Spieler hinzufügen
            if len(lobby.get('players', [])) == 0:
                symbol = 'X'
            else:
                symbol = 'O'
            
            player = {
                'name': player_name, 
                'symbol': symbol,
                'hash': get_player_hash(player_name, request.remote_addr),
                'ip': request.remote_addr
            }
            lobby['players'].append(player)
            
            # Spiel starten wenn 2 Spieler da sind
            if len(lobby['players']) == 2:
                lobby['started'] = True
                lobby['turn'] = lobby['players'][0]['name']
            
            return redirect(f'/lobby/{lobby["id"]}?player={player_name}')
    
    return render_template('error.html', 
                         message="Code ungültig oder Lobby existiert nicht."), 404

# ---------------------------------
# CHAT ROUTES
# ---------------------------------
@app.route('/poll/<room_id>', methods=['GET'])
def poll(room_id):
    lobby = lobbies.get(room_id)
    if not lobby:
        return jsonify({'error': 'Lobby existiert nicht.'})
    
    return jsonify(lobby)

@app.route('/move/<room_id>/<int:index>', methods=['POST'])
def move(room_id, index):
    name = request.form.get('name')
    lobby = lobbies.get(room_id)
    
    if not lobby:
        return jsonify({'error': 'Lobby existiert nicht.'})
    
    if lobby.get('winner'):
        return jsonify({'error': 'Spiel ist bereits beendet.'})
    
    if not lobby.get('started'):
        return jsonify({'error': 'Warte auf zweiten Spieler.'})
    
    if index < 0 or index > 8 or lobby.get('board', [""]*9)[index] != "":
        return jsonify({'error': 'Ungültiger Zug.'})

    # Spieler finden
    player = next((p for p in lobby.get('players', []) if p.get('name') == name), None)
    if not player:
        return jsonify({'error': 'Spieler nicht gefunden.'})

    # Prüfen ob dran
    if lobby.get('turn') != player.get('name'):
        return jsonify({'error': 'Nicht dein Zug.'})

    # Zug ausführen
    lobby['board'][index] = player.get('symbol')

    # Prüfen auf Sieg/Unentschieden
    winner = check_winner(lobby.get('board', []))
    if winner:
        lobby['winner'] = winner
    
    if not winner:
        # Nächster Spieler
        other_player = next(p for p in lobby.get('players', []) if p.get('name') != player.get('name'))
        lobby['turn'] = other_player.get('name')

    return jsonify(lobby)

@app.route('/chat/<room_id>/send', methods=['POST'])
def send_chat_message(room_id):
    name = request.form.get('name')
    message = request.form.get('message', '').strip()
    
    if not message:
        return jsonify({'error': 'Nachricht darf nicht leer sein.'})
    
    if len(message) > 500:
        return jsonify({'error': 'Nachricht zu lang (max. 500 Zeichen).'})
    
    lobby = lobbies.get(room_id)
    if not lobby:
        return jsonify({'error': 'Lobby existiert nicht.'})
    
    # Prüfen ob Spieler in der Lobby ist
    player = next((p for p in lobby.get('players', []) if p.get('name') == name), None)
    if not player:
        return jsonify({'error': 'Spieler nicht in Lobby.'})
    
    # Nachricht zensieren, wenn nötig
    banned_word = contains_banned_words(message)
    if banned_word:
        message = sanitize_message(message)
    
    # Nachricht speichern
    with chat_lock:
        lobby.setdefault('chat', []).append({
            'sender': name,
            'message': message,
            'time': time.time(),
            'type': 'chat',
            'censored': banned_word is not None
        })
    
    return jsonify({'success': True, 'censored': banned_word is not None})

@app.route('/chat/<room_id>/get', methods=['GET'])
def get_chat_messages(room_id):
    lobby = lobbies.get(room_id)
    if not lobby:
        return jsonify({'error': 'Lobby existiert nicht.'})
    
    return jsonify({'chat': lobby.get('chat', [])})

@app.route('/leave/<room_id>/<player_name>', methods=['POST'])
def leave_lobby(room_id, player_name):
    lobby = lobbies.get(room_id)
    if lobby:
        # Spieler entfernen
        lobby['players'] = [p for p in lobby.get('players', []) if p.get('name') != player_name]
        
        # Wenn Spieler verlässt, Spiel beenden
        if len(lobby.get('players', [])) == 1 and lobby.get('started'):
            lobby['winner'] = lobby['players'][0].get('name')  # Verbleibender Spieler gewinnt
        
        # Spiel zurücksetzen wenn nicht gestartet
        if len(lobby.get('players', [])) < 2:
            lobby['started'] = False
            lobby['turn'] = None
        
        # Wenn kein Spieler mehr da, Lobby löschen
        if len(lobby.get('players', [])) == 0:
            del lobbies[room_id]
            
        return jsonify({'success': True})
    
    return jsonify({'error': 'Lobby nicht gefunden'})

# ---------------------------------
# HILFSFUNKTION
# ---------------------------------
def check_winner(board):
    if not board or len(board) != 9:
        return None
    
    combos = [
        [0,1,2],[3,4,5],[6,7,8],
        [0,3,6],[1,4,7],[2,5,8],
        [0,4,8],[2,4,6]
    ]
    for c in combos:
        if board[c[0]] and board[c[0]] == board[c[1]] == board[c[2]]:
            return board[c[0]]
    if all(board):
        return "Draw"
    return None

# ---------------------------------
# ERROR HANDLER
# ---------------------------------
@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', message="Seite nicht gefunden."), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', message="Interner Server Fehler."), 500

# ---------------------------------
if __name__ == '__main__':
    app.run(debug=True, port=5000)
