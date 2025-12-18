from app import app, db, Admin
from werkzeug.security import generate_password_hash

with app.app_context():
    # Create tables if they don't exist
    db.create_all()

    username = input("Enter admin username: ")
    password = input("Enter admin password: ")
    
    hashed_password = generate_password_hash(password)
    admin = Admin(username=username, password_hash=hashed_password)
    
    db.session.add(admin)
    db.session.commit()
    
    print(f"✅ Admin user '{username}' created successfully!")
