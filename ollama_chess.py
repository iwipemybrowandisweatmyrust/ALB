import time
import json
import random
import berserk
import ollama
import chess
import logging

# Silence generic network warnings
logging.getLogger("urllib3").setLevel(logging.ERROR)

# --- CONFIGURATION ---
LICHESS_TOKEN = "INSERT API HERE"
MODEL_NAME = "INSERT EXACT BOT NAME HERE" 

session = berserk.TokenSession(LICHESS_TOKEN)
client = berserk.Client(session=session)

# Global tracker to ensure we only move ONCE per unique board state
last_processed_move_count = -1

try:
    BOT_USER_ID = client.account.get()['id']
    print(f"Logged in successfully! Bot User ID: {BOT_USER_ID}")
except Exception as e:
    print(f"Authentication failed: {e}")
    exit(1)

def get_ollama_move(board, legal_moves):
    """Prompts Ollama for a move with strict safety fallbacks."""
    print(f"[{MODEL_NAME}] Thinking...")
    
    prompt = (
        f"You are an expert chess engine. Current FEN: {board.fen()}.\n"
        f"Legal moves list: {', '.join(legal_moves)}.\n"
        f"Pick the absolute best legal move from the list. Respond ONLY with a JSON object "
        f"matching this schema: {{\"move\": \"your_chosen_move\"}}."
    )
    
    start_time = time.time()
    try:
        response = ollama.generate(model=MODEL_NAME, prompt=prompt, format='json')
        duration = time.time() - start_time
        print(f"[{MODEL_NAME}] Took {duration:.2f} seconds to think.")
        
        data = json.loads(response['response'].strip())
        ai_move = data.get('move', '').strip().lower()
        
        if ai_move in legal_moves:
            return ai_move
        else:
            fallback = random.choice(legal_moves)
            print(f"[{MODEL_NAME}] Hallucinated '{ai_move}'. Playing fallback: {fallback}")
            return fallback
            
    except Exception as e:
        fallback = random.choice(legal_moves)
        print(f"[{MODEL_NAME}] Error ({e}). Defaulting to random move: {fallback}")
        return fallback

def play_game(game_id):
    """Handles the active game loop and safely filters out streaming duplicate events."""
    global last_processed_move_count
    print(f"Starting game: {game_id}")
    bot_color = None
    last_processed_move_count = -1  # Reset move count tracking for the new match
    
    for event in client.bots.stream_game_state(game_id):
        if event['type'] == 'gameFull':
            state = event['state']
            if event['white'].get('id') == BOT_USER_ID:
                bot_color = chess.WHITE
                print("Bot assigned as WHITE.")
            else:
                bot_color = chess.BLACK
                print("Bot assigned as BLACK.")
        elif event['type'] == 'gameState':
            state = event
        else:
            continue

        if state['status'] != 'started':
            print(f"Game {game_id} finished. Status: {state['status']}")
            break

        # Rebuild the board from the game move history string
        board = chess.Board()
        played_moves = state['moves'].split() if state['moves'] else []
        current_move_count = len(played_moves)
        
        for move in played_moves:
            board.push_uci(move)

        # CRITICAL PROTECTION: Turn detection combined with move count verification
        if board.turn == bot_color and current_move_count > last_processed_move_count:
            # Update the tracker immediately BEFORE thinking to block rapid duplicate stream events
            last_processed_move_count = current_move_count
            
            legal_moves = [move.uci() for move in board.legal_moves]
            chosen_move = get_ollama_move(board, legal_moves)
            
            try:
                client.bots.make_move(game_id, chosen_move)
                print(f"Played move: {chosen_move}\n")
                
                # Update tracker again to equal the newly updated board state count
                last_processed_move_count = current_move_count + 1
            except Exception as e:
                print(f"Failed to submit move {chosen_move}: {e}")
                # Reset tracker on failure so the bot can try again on the next event pump
                last_processed_move_count = current_move_count - 1

def main_loop():
    """Infinitely listens for games until you press Ctrl+C."""
    print(f"[{MODEL_NAME}] Online,waiting for lichess game...")
    
    while True:
        try:
            for event in client.bots.stream_incoming_events():
                if event['type'] == 'challenge':
                    challenge_id = event['challenge']['id']
                    client.bots.accept_challenge(challenge_id)
                    print(f"Accepted challenge {challenge_id}")
                elif event['type'] == 'gameStart':
                    game_id = event['game']['id']
                    play_game(game_id)
        except Exception as e:
            print(f"Error encountered: {e}. Reconnecting in 5 seconds...")
            time.sleep(5)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\nBot turned off successfully.")
