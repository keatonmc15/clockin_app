from app import app, db, Store, Employee

with app.app_context():  # <-- ensures Flask knows which app to use

    # -------------------------
    # Clear existing data (optional)
    # -------------------------
    print("Clearing existing data...")
    db.drop_all()
    db.create_all()

    # -------------------------
    # Create Test Store
    # -------------------------
    store = Store(
        name="Test Store",
        latitude=36.15398,
        longitude=-95.99277,
        radius_meters=200
    )
    db.session.add(store)

    # -------------------------
    # Create Test Employees
    # -------------------------
    employees = [
        Employee(name="Alice Test", qr_code="ALICE123"),
        Employee(name="Bob Test", qr_code="BOB123")
    ]

    db.session.add_all(employees)

    # -------------------------
    # Commit all changes
    # -------------------------
    db.session.commit()

    print("✅ Seed data created successfully!")
    print(f"Store: {store.name}")
    for emp in employees:
        print(f"Employee: {emp.name}, QR: {emp.qr_code}")
