import requests
import time
import uuid
import os

BASE_URL = os.environ.get("MR_BASE_URL", "http://localhost:8000")

def auth_headers():
    username = os.environ.get("MR_TEST_USERNAME")
    password = os.environ.get("MR_TEST_PASSWORD")
    if not username or not password:
        raise RuntimeError("Set MR_TEST_USERNAME and MR_TEST_PASSWORD to a real editor/owner account")
    response = requests.post(f"{BASE_URL}/api/auth/token", data={"username": username, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

def run_test():
    headers = auth_headers()
    print("--- 1. TV generates pairing code ---")
    device_id = str(uuid.uuid4())
    res = requests.post(f"{BASE_URL}/api/screens/register", json={"device_id": device_id})
    print(f"Register API: {res.status_code} {res.json()}")
    assert res.status_code == 200
    pair_code = res.json()["pair_code"]

    print("\n--- 2. Pair TV from dashboard ---")
    res = requests.post(f"{BASE_URL}/api/screens/pair", headers=headers, json={"pair_code": pair_code})
    print(f"Pair API: {res.status_code} {res.json()}")
    assert res.status_code == 200

    print("\n--- 3. Upload image ---")
    with open("test_image.png", "wb") as f:
        f.write(b"fake image data")
        
    with open("test_image.png", "rb") as f:
        res = requests.post(f"{BASE_URL}/api/content/upload", headers=headers, files={"file": f}, data={"name": "Test Image"})
    if res.status_code != 201:
        print(f"Upload API Failed: {res.status_code} {res.text}")
        raise AssertionError(f"upload returned {res.status_code}, expected 201")
    print(f"Upload API: {res.status_code} {res.json()}")
    content_id = res.json()["id"]
    file_url = res.json()["file_url"]

    print("\n--- 4. Create playlist ---")
    res = requests.post(f"{BASE_URL}/api/playlists/", headers=headers, json={"name": "My E2E Playlist"})
    print(f"Create Playlist API: {res.status_code} {res.json()}")
    assert res.status_code == 201, f"expected 201 Created, got {res.status_code}"
    playlist_id = res.json()["id"]

    print("\n--- 5. Add content to playlist ---")
    res = requests.post(f"{BASE_URL}/api/playlists/{playlist_id}/items", headers=headers, json={
        "content_id": content_id,
        "duration": 10,
        "order": 0
    })
    print(f"Add Item API: {res.status_code} {res.json()}")
    assert res.status_code == 201, f"expected 201 Created, got {res.status_code}"

    print("\n--- 6. Assign playlist to screen ---")
    # First we need the screen id. We only have device_id.
    res = requests.get(f"{BASE_URL}/api/screens/", headers=headers)
    screens = res.json()
    screen_id = next(s["id"] for s in screens if s["device_id"] == device_id)
    
    res = requests.post(f"{BASE_URL}/api/screens/{screen_id}/assign/{playlist_id}", headers=headers)
    print(f"Assign API: {res.status_code} {res.json()}")
    assert res.status_code == 200

    print("\n--- 7. TV syncs playlist ---")
    res = requests.get(f"{BASE_URL}/api/screens/{device_id}/sync")
    print(f"Sync API: {res.status_code} {res.json()}")
    assert res.status_code == 200
    sync_data = res.json()
    assert sync_data["playlist"]["items"][0]["content"]["id"] == content_id

    print("\n--- 8. TV downloads content ---")
    print(f"TV would download from: {file_url}")
    # Simulate download
    res = requests.get(file_url)
    print(f"Download API: {res.status_code}")
    assert res.status_code == 200
    print("Content successfully downloaded to cache")

    print("\n--- 9. Heartbeat ---")
    res = requests.post(f"{BASE_URL}/api/screens/heartbeat", json={
        "device_id": device_id,
        "device_version": "1.0",
        "storage_used": "1.2 GB / 8.0 GB"
    })
    print(f"Heartbeat API: {res.status_code} {res.json()}")
    assert res.status_code == 200
    
    print("\n--- E2E TEST PASSED ---")

if __name__ == "__main__":
    try:
        run_test()
    except Exception as e:
        print(f"Error: {e}")
