from fastapi.testclient import TestClient
from src.api.app import app

client = TestClient(app)

def main():
    hist = client.get('/chat/history').json()
    print('Initial history:', hist)
    resp = client.post('/chat', json={'message':'Hallo – funktioniert der Chat?'}).json()
    print('After sending message:', resp)

if __name__ == '__main__':
    main()
