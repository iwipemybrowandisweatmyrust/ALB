import time
import json
import random
import berserk
import ollama
import chess
import logging

# Completely silence generic network and library warning logs in the terminal
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("berserk").setLevel(logging.ERROR)

# --- CONFIGURATION ---
LICHESS_TOKEN = "API_KEY_HERE"
MODEL_NAME = "MODEL_NAME_HERE" 

session = berserk.TokenSession(LICHESS_TOKEN)
client = berserk.Client(session=session)

# Global tracker to ensure we only move ONCE per unique board state
last_processed_move_count = -1

try:
    BOT_USER_ID = client.account.get()['id']
    print(f"Logged in as {BOT_USER_ID}")
except Exception as e:
    print(f"Authentication failed: {e}")
    exit(1)

def send_lichess_chat(game_id, message):
    """Safely sends a message to the Lichess game chat without breaking the loop."""
    try:
        client.board.write_chat_message(game_id, room="player", text=message)
    except Exception:
        pass  # Ignore chat errors to keep the main game running smoothly

def get_ollama_move(game_id, board, legal_moves):
    """Prompts Ollama for a move. Hides errors from terminal and routes them to Lichess chat."""
    print(f"[{MODEL_NAME}]  Is thinking...")
    
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
            # Route hallucination error to Lichess chat instead of terminal
            send_lichess_chat(
                game_id, 
                f"🤖 [System Notice]: {MODEL_NAME} hallucinated an illegal move '{ai_move}'. Playing random fallback: {fallback}"
            )
            return fallback
            
    except Exception as e:
        fallback = random.choice(legal_moves)
        # Route connection/parsing errors to Lichess chat instead of terminal
        send_lichess_chat(
            game_id, 
            f"🤖 [System Error]: {str(e)[:80]}. Defaulting to random move: {fallback}"
        )
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
            
            # Send a friendly greeting in chat at game start
            send_lichess_chat(game_id, f"{MODEL_NAME} is playing.")
            
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

        # Turn detection combined with move count verification
        if board.turn == bot_color and current_move_count > last_processed_move_count:
            last_processed_move_count = current_move_count
            
            legal_moves = [move.uci() for move in board.legal_moves]
            # Pass the game_id into the calculation function so it can access the chat
            chosen_move = get_ollama_move(game_id, board, legal_moves)
            
            try:
                client.bots.make_move(game_id, chosen_move)
                print(f"Played move: {chosen_move}\n")
                last_processed_move_count = current_move_count + 1
            except Exception as e:
                # Send the final submission failure to chat if Lichess rejects the API request
                send_lichess_chat(game_id, f"🤖 [Submission Error]: Failed to push {chosen_move}. Retrying...")
                last_processed_move_count = current_move_count - 1

def main_loop():
    """Press Ctrl C to cancel."""
    print(f"[{MODEL_NAME}] Waiting for a game...")
    
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
            # Keep terminal loop stable but pause briefly if a major internet drop happens
            time.sleep(5)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\nBot turned off successfully.")
