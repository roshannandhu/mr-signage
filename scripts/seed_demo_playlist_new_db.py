import sys, os, glob
sys.path.insert(0, os.path.abspath("."))
from backend.database import SessionLocal
from backend.models import Organization, User, Content, Playlist, PlaylistItem, Screen

def main():
    db = SessionLocal()
    org = db.query(Organization).first()
    
    # 1. Real media items present in uploads/
    uploads_dir = os.path.abspath("uploads")
    media_items_to_create = [
        ("PRAKRITHI ROOTS Commercial Ad", "dc18e605-d27c-45ad-b18e-824301fcd6a9.png", "image/png", 10),
        ("Msolar Clean Energy Video", "f9863204-f997-4122-ac1b-a50157e3d905.mp4", "video/mp4", 44),
        ("Third Eye Smart Electronics", "7fe73bb0-2c80-4d87-a573-fabb2ac967c1.png", "image/png", 10),
        ("Showroom Video Commercial", "edd89455-5bf6-46b5-bc4c-7d7f824d9f9a.mp4", "video/mp4", 30),
    ]
    
    # Clean previous demo items
    db.query(PlaylistItem).delete()
    db.query(Playlist).delete()
    db.query(Content).delete()
    db.commit()
    
    contents = []
    for title, fname, ctype, dur in media_items_to_create:
        fpath = os.path.join(uploads_dir, fname)
        fsize = os.path.getsize(fpath) if os.path.exists(fpath) else 18560
        c = Content(
            organization_id=org.id,
            name=title,
            type=ctype,
            file_url=f"/uploads/{fname}",
            file_size_bytes=fsize,
            status="ready",
            duration_ms=dur * 1000
        )
        db.add(c)
        contents.append(c)
    db.flush()
    print(f"Created {len(contents)} media assets in new Supabase DB")
    
    # 2. Create Playlist
    playlist = Playlist(
        organization_id=org.id,
        name="Showroom Commercial Loop",
        default_transition="fade",
        default_transition_ms=600
    )
    db.add(playlist)
    db.flush()
    
    for idx, c in enumerate(contents):
        item = PlaylistItem(
            playlist_id=playlist.id,
            content_id=c.id,
            order=idx,
            duration=c.duration_ms // 1000 if c.duration_ms else 10
        )
        db.add(item)
    db.flush()
    print(f"Created Playlist '{playlist.name}' (ID: {playlist.id}) with {len(contents)} items")
    
    # 3. Assign playlist to Screen 1
    screen = db.query(Screen).filter(Screen.id == 1).first()
    if screen:
        screen.playlist_id = playlist.id
        print(f"Assigned Playlist '{playlist.name}' to Screen ID {screen.id}")
    
    db.commit()
    db.close()
    print("Demo playlist and screen assignment completed on new Supabase DB!")

if __name__ == "__main__":
    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("DELETE all tenants' content and playlists, then reseed a demo playlist", SQLALCHEMY_DATABASE_URL)
    main()
