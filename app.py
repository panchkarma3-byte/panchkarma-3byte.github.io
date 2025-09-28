# app.py

import firebase_admin
import os
import json
import uuid
import razorpay
import random
from firebase_admin import credentials, firestore, auth
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from datetime import datetime, timedelta, timezone

# Initialize Flask App
app = Flask(__name__)
app.secret_key = 'your_super_secret_key'

# --- Razorpay Configuration ---
app.config['RAZORPAY_KEY_ID'] = 
app.config['RAZORPAY_KEY_SECRET'] = 

razorpay_client = razorpay.Client(
    auth=(app.config['RAZORPAY_KEY_ID'], app.config['RAZORPAY_KEY_SECRET'])
)

# Use a default config if not running in the Canvas environment
__firebase_config_str = os.environ.get('FIREBASE_CONFIG')
if not __firebase_config_str:
    __firebase_config_str = json.dumps({
        "apiKey": "AIzaSyA-i3OVMerzPjPDkH5fOZsUbVt0Fc7uDRE",
        "authDomain": "panchakarma-c8505.firebaseapp.com",
        "projectId": "panchakarma-c8505",
        "storageBucket": "panchakarma-c8505.firebasestorage.app",
        "messagingSenderId": "382892805989",
        "appId": "1:382892805989:web:618037c19529563f995b8c"
    })
firebase_config = json.loads(__firebase_config_str)

# Get the absolute path to the service account key
key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'serviceAccountKey.json')

# Initialize Firebase Admin SDK
db = None
try:
    cred = credentials.Certificate(key_path)
    if not firebase_admin._apps:
        firebase_app = firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase Admin SDK initialized successfully.")
except Exception as e:
    print(f"Warning: Could not initialize Firebase Admin SDK. Error: {e}")
    if firebase_admin._apps:
        db = firestore.client()

# References to your collections
if db:
    users_ref = db.collection('users')
    practitioners_ref = db.collection('practitioners')
    sessions_ref = db.collection('sessions')
    notifications_ref = db.collection('notifications')
    feedback_ref = db.collection('feedback')
    availability_ref = db.collection('practitioner_availability')
else:
    users_ref, practitioners_ref, sessions_ref, notifications_ref, feedback_ref, availability_ref = None, None, None, None, None, None


# NEW HELPER FUNCTION TO CREATE PATIENT JOURNEY
def create_patient_journey(session_id):
    """
    Generates a personalized therapy journey for a patient based on a template.
    Triggered after a session payment is confirmed.
    """
    if not db:
        print("Database not available, skipping journey creation.")
        return

    try:
        session_ref = sessions_ref.document(session_id)
        session_doc = session_ref.get()
        if not session_doc.exists:
            print(f"Session {session_id} not found.")
            return

        session_data = session_doc.to_dict()
        therapy_type = session_data.get('therapy', '').lower()
        session_date = session_data.get('date')
        patient_uid = session_data.get('patient_uid')
        
        # 1. Fetch the therapy plan template
        plan_ref = db.collection('therapy_plans').document(therapy_type)
        plan_doc = plan_ref.get()
        
        if not plan_doc.exists:
            print(f"No therapy plan found for '{therapy_type}'.")
            return

        plan_data = plan_doc.to_dict()
        tasks_template = plan_data.get('tasks', [])
        
        # 2. Generate personalized tasks with specific dates
        journey_tasks = []
        for task_template in tasks_template:
            day_offset = task_template.get('day_offset', 0)
            task_date = session_date + timedelta(days=day_offset)
            journey_tasks.append({
                "title": task_template.get('title'),
                "description": task_template.get('description'),
                "task_date": task_date,
                "status": "pending" # Initial status
            })

        # 3. Save the new journey to the patient_journeys collection
        journey_ref = db.collection('patient_journeys').document(session_id)
        journey_ref.set({
            "patient_uid": patient_uid,
            "session_id": session_id,
            "plan_name": plan_data.get('planName'),
            "therapy_type": therapy_type.capitalize(),
            "session_date": session_date,
            "tasks": journey_tasks
        })
        print(f"Successfully created journey for session {session_id}.")

    except Exception as e:
        print(f"Error creating patient journey for session {session_id}: {e}")


@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        name = request.form['name']
        number = request.form['number']
        role = request.form['role']
        consent = 'privacy-consent' in request.form
        
        if not consent:
            return render_template('register.html', error="You must agree to the Privacy Policy to register.")
        
        if not db:
            return render_template('register.html', error="Server configuration error: Database not available.")
        
        try:
            user = auth.create_user(email=email, password=password)
            if role == 'patient':
                users_ref.document(user.uid).set({
                    'email': email, 'name': name, 'number': number, 'role': 'patient', 'created_at': datetime.now()
                })
            elif role == 'practitioner':
                practitioners_ref.document(user.uid).set({
                    'email': email, 'name': name, 'number': number, 'role': 'practitioner',
                    'created_at': datetime.now(), 
                    'verification_status': 'Pending Review',
                    'specialties': [], 'address': 'Not specified',
                    'contact': {'phone': number, 'email': email}, 
                    'appointment_price': 0, 'session_price': 0
                })
                # Initialize availability document with new structure
                availability_ref.document(user.uid).set({'recurring': {}, 'overrides': {}})
            return redirect(url_for('signin'))
        except Exception as e:
            return render_template('register.html', error=str(e))
    return render_template('register.html')

@app.route('/signin', methods=['GET'])
def signin():
    return render_template('signin.html', firebase_config=firebase_config)

@app.route('/verify-token', methods=['POST'])
def verify_token():
    id_token = request.json.get('idToken')
    if not id_token:
        return jsonify({"success": False, "error": "No ID token provided."}), 400
    if not db:
        return jsonify({"success": False, "error": "Server configuration error."}), 500

    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        
        admin_doc = practitioners_ref.document(uid).get()
        if admin_doc.exists and admin_doc.to_dict().get('role') == 'admin':
            session['user_id'] = uid
            session['user_role'] = 'admin'
            return jsonify({"success": True, "redirect": url_for('admin_dashboard')})
            
        user_doc = users_ref.document(uid).get()
        user_role = None
        if user_doc.exists:
            user_role = user_doc.to_dict().get('role')
        else:
            practitioner_doc = practitioners_ref.document(uid).get()
            if practitioner_doc.exists:
                user_role = practitioner_doc.to_dict().get('role')
            else:
                users_ref.document(uid).set({
                    'email': decoded_token.get('email', 'N/A'),
                    'name': decoded_token.get('name', 'New User'),
                    'role': 'patient', 'created_at': datetime.now()
                })
                user_role = 'patient'
        
        if not user_role:
            return jsonify({"success": False, "error": "User role not found."}), 401

        session['user_id'] = uid
        session['user_role'] = user_role
        return jsonify({"success": True, "redirect": url_for('dashboard')})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 401

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('signin'))

    user_role, user_id = session.get('user_role'), session.get('user_id')
    
    if user_role == 'admin':
        return redirect(url_for('admin_dashboard'))

    if not db:
        flash("Database access is currently unavailable.", "error")
        return render_template('dashboard.html', sessions=[], notifications=[], user_role=user_role, user_id=user_id, user_settings={}, user_profile={}, firebase_config=firebase_config, feedback=[], availability={}, journeys=[])

    try:
        practitioners_docs = practitioners_ref.stream()
        practitioner_map = {doc.id: doc.to_dict() for doc in practitioners_docs}
        
        patient_docs = users_ref.where('role', '==', 'patient').stream()
        patient_map = {doc.id: doc.to_dict() for doc in patient_docs}
        
        therapists_data = [{'doc_id': doc_id, **data} for doc_id, data in practitioner_map.items()]
    except Exception as e:
        flash(f"Error fetching user data: {e}", "error")
        practitioner_map, patient_map, therapists_data = {}, {}, []

    user_settings_doc = notifications_ref.document(user_id).get()
    user_settings = user_settings_doc.to_dict() if user_settings_doc.exists else {'in-app': True, 'sms': False, 'email': False}

    user_profile = {}
    if user_role == 'patient':
        user_doc = users_ref.document(user_id).get()
        if user_doc.exists:
            user_profile = user_doc.to_dict()
    elif user_role == 'practitioner':
        practitioner_doc = practitioners_ref.document(user_id).get()
        if practitioner_doc.exists:
            user_profile = practitioner_doc.to_dict()

    sessions_data, notifications, feedback_data, active_patients_count, availability_data, journeys_data = [], [], [], 0, {}, []

    try:
        if user_role == 'practitioner':
            availability_doc = availability_ref.document(user_id).get()
            if availability_doc.exists:
                availability_data = availability_doc.to_dict()

            all_sessions_query = sessions_ref.where('practitioner_uid', '==', user_id).order_by('date', direction=firestore.Query.DESCENDING).stream()
            active_patient_uids = set()
            for doc in all_sessions_query:
                session_item = doc.to_dict()
                if not session_item.get('date'):
                    print(f"Warning: Practitioner session document {doc.id} is missing a date and will be skipped.")
                    continue
                
                session_item['doc_id'] = doc.id
                patient_uid = session_item.get('patient_uid')
                if patient_uid:
                    active_patient_uids.add(patient_uid)
                    patient_info = patient_map.get(patient_uid, {})
                    session_item['patient_name'] = patient_info.get('name', 'Unknown Patient')
                else:
                    session_item['patient_name'] = 'N/A'
                
                sessions_data.append(session_item)
            active_patients_count = len(active_patient_uids)

            feedback_query = feedback_ref.where('practitioner_uid', '==', user_id).order_by('created_at', direction=firestore.Query.DESCENDING).stream()
            for doc in feedback_query:
                fb = doc.to_dict()
                patient_info = patient_map.get(fb.get('patient_uid'), {})
                fb['patient_name'] = patient_info.get('name', 'Unknown Patient')
                feedback_data.append(fb)
            
        elif user_role == 'patient':
            patient_sessions_query = sessions_ref.where('patient_uid', '==', user_id).order_by('date', direction=firestore.Query.DESCENDING).stream()
            now_utc = datetime.now(timezone.utc)
            for doc in patient_sessions_query:
                session_item = doc.to_dict()
                if not session_item.get('date'):
                    continue

                session_item['doc_id'] = doc.id
                practitioner_info = practitioner_map.get(session_item.get('practitioner_uid'), {})
                session_item['practitioner_name'] = practitioner_info.get('name', 'N/A')
                
                session_date = session_item.get('date')
                session_status = session_item.get('status')

                if session_status == 'payment_pending':
                    session_item['payment_deadline_passed'] = (session_date - now_utc) < timedelta(days=1)
                
                session_item['is_cancellable'] = False
                if session_status == 'payment_pending' and (session_date - now_utc) > timedelta(days=3):
                    session_item['is_cancellable'] = True
                
                session_item['is_reschedulable'] = False
                if session_status in ['payment_pending', 'scheduled'] and (session_date - now_utc) > timedelta(days=1):
                    session_item['is_reschedulable'] = True
                
                sessions_data.append(session_item)

            # Fetch active patient journeys
            try:
                active_journeys_query = db.collection('patient_journeys') \
                                          .where('patient_uid', '==', user_id).stream()
                for doc in active_journeys_query:
                    journeys_data.append(doc.to_dict())
            except Exception as e:
                flash(f"Could not load therapy journeys: {e}", "error")

        notifications_query = notifications_ref.where('recipient_id', '==', user_id).order_by('created_at', direction=firestore.Query.DESCENDING).stream()
        for doc in notifications_query:
            notification_item = doc.to_dict()
            if notification_item.get('created_at'):
                notifications.append(notification_item)

    except Exception as e:
        flash(f"An error occurred while fetching dashboard data: {e}", "error")

    return render_template('dashboard.html', 
                           sessions=sessions_data, 
                           notifications=notifications, 
                           user_role=user_role, 
                           user_id=user_id, 
                           user_settings=user_settings, 
                           therapists=therapists_data, 
                           user_profile=user_profile, 
                           firebase_config=firebase_config,
                           feedback=feedback_data,
                           active_patients_count=active_patients_count,
                           availability=availability_data,
                           journeys=journeys_data)


@app.route('/schedule_session_patient', methods=['POST'])
def schedule_session_patient():
    if 'user_id' not in session or session.get('user_role') != 'patient':
        return redirect(url_for('signin'))
    if not db:
        flash('Database is not available.', 'error')
        return redirect(url_for('dashboard'))
    try:
        practitioner_uid = request.form['therapist-uid']
        therapy_type = request.form['therapy-type']
        session_date = request.form['session-date']
        session_time = request.form['session-time']

        session_datetime_obj = datetime.strptime(f"{session_date} {session_time}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

        existing_session_query = sessions_ref.where('practitioner_uid', '==', practitioner_uid)\
                                             .where('date', '==', session_datetime_obj).limit(1).stream()
        if len(list(existing_session_query)) > 0:
            flash('This time slot has just been booked. Please select another time.', 'error')
            return redirect(url_for('dashboard'))

        if therapy_type == 'auto':
            therapies = ['Virechana', 'Nasya', 'Basti', 'Vamana', 'Raktamokshana']
            therapy_type = random.choice(therapies)

        practitioner_doc = practitioners_ref.document(practitioner_uid).get()
        practitioner_data = practitioner_doc.to_dict()
        appointment_price = practitioner_data.get('appointment_price', 0)
        session_price = practitioner_data.get('session_price', 0)

        sessions_ref.add({
            'patient_uid': session['user_id'], 'practitioner_uid': practitioner_uid,
            'therapy': therapy_type, 'date': session_datetime_obj,
            'status': 'payment_pending', 'payment_status': 'pending',
            'amount_due': appointment_price,
            'appointment_price': appointment_price,
            'session_price': session_price,
            'created_at': datetime.now(timezone.utc)
        })

        notifications_ref.add({
            'recipient_id': practitioner_uid, 'message': f"A new session has been requested by a patient.",
            'type': 'new_request', 'read': False, 'created_at': datetime.now(timezone.utc)
        })
        flash('Your appointment request has been sent! Please pay the confirmation fee to confirm.', 'success')
        return redirect(url_for('dashboard'))
    except Exception as e:
        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('dashboard'))

@app.route('/create_order', methods=['POST'])
def create_order():
    if 'user_id' not in session or session.get('user_role') != 'patient':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    session_id = request.json.get('session_id')
    if not session_id:
        return jsonify({"success": False, "error": "Session ID not provided"}), 400
    try:
        session_doc = sessions_ref.document(session_id).get()
        if not session_doc.exists:
            return jsonify({"success": False, "error": "Session not found"}), 404
        session_data = session_doc.to_dict()
        amount_due = session_data.get('amount_due', 0)
        if amount_due <= 0:
            return jsonify({"success": False, "error": "No payment is due for this session."}), 400
        order_data = {
            'amount': int(amount_due * 100), 'currency': 'INR', 'receipt': f'receipt_{session_id}'
        }
        order = razorpay_client.order.create(data=order_data)
        return jsonify({
            "success": True, "order_id": order['id'], "amount": order['amount'],
            "key_id": app.config['RAZORPAY_KEY_ID']
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/verify_payment', methods=['POST'])
def verify_payment():
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    data = request.json
    session_id = data.get('session_id')
    try:
        params_dict = {
            'razorpay_order_id': data['razorpay_order_id'],
            'razorpay_payment_id': data['razorpay_payment_id'],
            'razorpay_signature': data['razorpay_signature']
        }
        razorpay_client.utility.verify_payment_signature(params_dict)
        session_ref = sessions_ref.document(session_id)
        session_ref.update({
            'status': 'scheduled', 'payment_status': 'paid', 'payment_id': data['razorpay_payment_id']
        })
        
        # Trigger the journey creation
        create_patient_journey(session_id)
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": "Payment verification failed"}), 400

@app.route('/complete_session', methods=['POST'])
def complete_session():
    if 'user_id' not in session or session.get('user_role') != 'practitioner':
        return "Unauthorized", 403
    session_id = request.form.get('session_id')
    try:
        sessions_ref.document(session_id).update({'status': 'completed'})
        flash("Session marked as complete.", "success")
        return redirect(url_for('dashboard'))
    except Exception as e:
        flash(f"Error completing session: {str(e)}", "error")
        return redirect(url_for('dashboard'))

@app.route('/cancel_session_patient', methods=['POST'])
def cancel_session_patient():
    if 'user_id' not in session or session.get('user_role') != 'patient':
        flash("Unauthorized access.", "error")
        return redirect(url_for('signin'))
    
    session_id = request.form.get('session_id')
    session_ref = sessions_ref.document(session_id)
    session_doc = session_ref.get()

    if not session_doc.exists:
        flash("Session not found.", "error")
        return redirect(url_for('dashboard'))

    session_data = session_doc.to_dict()
    
    is_cancellable = False
    if session_data.get('status') == 'payment_pending':
        session_date = session_data.get('date')
        if (session_date - datetime.now(timezone.utc)) > timedelta(days=3):
            is_cancellable = True

    if not is_cancellable:
        flash("This appointment cannot be cancelled.", "error")
        return redirect(url_for('dashboard'))

    try:
        session_ref.update({'status': 'cancelled'})
        flash("Your appointment request has been cancelled.", "success")
    except Exception as e:
        flash(f"An error occurred: {e}", "error")
        
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/save_notifications', methods=['POST'])
def save_notifications():
    if 'user_id' not in session:
        return redirect(url_for('signin'))
    if not db:
        flash("Database is not available.", "error")
        return redirect(url_for('dashboard'))
    user_settings = {
        'in-app': 'in-app' in request.form, 'sms': 'sms' in request.form, 'email': 'email' in request.form
    }
    notifications_ref.document(session['user_id']).set(user_settings, merge=True)
    flash("Notification preferences saved.", "success")
    return redirect(url_for('dashboard'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    user_id, user_role, data = session['user_id'], session['user_role'], request.json
    try:
        if user_role == 'patient':
            users_ref.document(user_id).update({'name': data.get('name'), 'number': data.get('number')})
        elif user_role == 'practitioner':
            updates = {
                'name': data.get('name'), 'number': data.get('number'),
                'address': data.get('address'), 
                'specialties': data.get('specialties', []),
                'appointment_price': int(data.get('appointment_price', 0)),
                'session_price': int(data.get('session_price', 0)),
                'contact.phone': data.get('number')
            }
            practitioners_ref.document(user_id).update({k: v for k, v in updates.items() if v is not None})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/update_recurring_availability', methods=['POST'])
def update_recurring_availability():
    if 'user_id' not in session or session.get('user_role') != 'practitioner':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    user_id = session['user_id']
    recurring_data = request.json
    try:
        availability_ref.document(user_id).set({'recurring': recurring_data}, merge=True)
        return jsonify({"success": True, "message": "Recurring schedule updated."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/update_date_override', methods=['POST'])
def update_date_override():
    if 'user_id' not in session or session.get('user_role') != 'practitioner':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    user_id = session['user_id']
    data = request.json
    date_str = data.get('date')
    times_str = data.get('times', '')
    times_list = sorted(list(set([t.strip() for t in times_str.split(',') if t.strip()])))
    try:
        availability_ref.document(user_id).update({
            f'overrides.{date_str}': times_list
        })
        return jsonify({"success": True, "message": f"Availability for {date_str} has been overridden."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/get_availability/<practitioner_uid>')
def get_availability(practitioner_uid):
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        availability_doc = availability_ref.document(practitioner_uid).get()
        if not availability_doc.exists:
            return jsonify({"success": True, "slots": {}})
        
        availability_data = availability_doc.to_dict()
        recurring_rules = availability_data.get('recurring', {})
        overrides = availability_data.get('overrides', {})

        booked_slots = {}
        start_of_today = datetime.now(timezone.utc)
        sessions_query = sessions_ref.where('practitioner_uid', '==', practitioner_uid)\
                                   .where('date', '>=', start_of_today).stream()
        for sess in sessions_query:
            sess_data = sess.to_dict()
            if sess_data.get('date') and sess_data.get('status') != 'cancelled':
                sess_date = sess_data['date'].strftime('%Y-%m-%d')
                sess_time = sess_data['date'].strftime('%H:%M')
                if sess_date not in booked_slots:
                    booked_slots[sess_date] = []
                booked_slots[sess_date].append(sess_time)

        final_slots = {}
        today = datetime.now(timezone.utc).date()
        for i in range(60): 
            current_date = today + timedelta(days=i)
            date_str = current_date.strftime('%Y-%m-%d')
            day_name = current_date.strftime('%A').lower()
            
            day_slots = []

            if date_str in overrides:
                day_slots = overrides.get(date_str, [])
            elif day_name in recurring_rules:
                rule = recurring_rules[day_name]
                try: 
                    start_str = rule.get('start')
                    end_str = rule.get('end')
                    if start_str and end_str:
                        start_time = datetime.strptime(start_str, '%H:%M').time()
                        end_time = datetime.strptime(end_str, '%H:%M').time()
                        interval = int(rule.get('interval') or 60)
                        
                        current_slot_time = datetime.combine(current_date, start_time)
                        while current_slot_time.time() < end_time:
                            day_slots.append(current_slot_time.strftime('%H:%M'))
                            current_slot_time += timedelta(minutes=interval)
                except (ValueError, TypeError) as e:
                    print(f"Warning: Skipping recurring rule for {day_name} due to malformed data: {rule}. Error: {e}")
                    pass
            
            if day_slots:
                booked_for_day = booked_slots.get(date_str, [])
                available_day_slots = [slot for slot in day_slots if slot not in booked_for_day]
                
                if available_day_slots:
                    final_slots[date_str] = available_day_slots

        return jsonify({"success": True, "slots": final_slots})
    except Exception as e:
        print(f"Error in get_availability for {practitioner_uid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/reschedule/<session_id>')
def reschedule_session(session_id):
    if 'user_id' not in session or session.get('user_role') != 'patient':
        return redirect(url_for('signin'))

    session_doc = sessions_ref.document(session_id).get()
    if not session_doc.exists:
        flash("Session not found.", "error")
        return redirect(url_for('dashboard'))

    session_data = session_doc.to_dict()
    if session_data['patient_uid'] != session['user_id']:
        flash("You are not authorized to reschedule this session.", "error")
        return redirect(url_for('dashboard'))
    
    user_id = session['user_id']
    user_doc = users_ref.document(user_id).get()
    user_profile = user_doc.to_dict() if user_doc.exists else {}
    
    practitioner_doc = practitioners_ref.document(session_data['practitioner_uid']).get()
    
    return render_template('reschedule.html', 
                           session_details=session_data,
                           session_id=session_id,
                           practitioner=practitioner_doc.to_dict(),
                           user_profile=user_profile,
                           firebase_config=firebase_config)

@app.route('/update_rescheduled_session', methods=['POST'])
def update_rescheduled_session():
    if 'user_id' not in session or session.get('user_role') != 'patient':
        return redirect(url_for('signin'))

    session_id = request.form['session_id']
    new_date = request.form['session-date']
    new_time = request.form['session-time']
    new_datetime = datetime.strptime(f"{new_date} {new_time}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

    session_ref = sessions_ref.document(session_id)
    
    try:
        @firestore.transactional
        def reschedule_transaction(transaction):
            session_doc = session_ref.get(transaction=transaction)
            if not session_doc.exists:
                raise ValueError("Session no longer exists.")
            
            session_data = session_doc.to_dict()
            practitioner_uid = session_data['practitioner_uid']
            old_datetime = session_data['date']

            availability_ref_tran = db.collection('practitioner_availability').document(practitioner_uid)
            availability_doc = availability_ref_tran.get(transaction=transaction)
            
            transaction.update(session_ref, {
                'date': new_datetime,
                'rescheduled': True
            })

            if availability_doc.exists:
                overrides = availability_doc.to_dict().get('overrides', {})
                old_date_str = old_datetime.strftime('%Y-%m-%d')
                old_time_str = old_datetime.strftime('%H:%M')
                
                if old_date_str in overrides:
                    if old_time_str not in overrides[old_date_str]:
                        overrides[old_date_str].append(old_time_str)
                        overrides[old_date_str].sort()
                else:
                    overrides[old_date_str] = [old_time_str]
                transaction.update(availability_ref_tran, {'overrides': overrides})

        transaction = db.transaction()
        reschedule_transaction(transaction)
        
        flash("Appointment rescheduled successfully!", "success")
        return redirect(url_for('dashboard'))

    except Exception as e:
        flash(f"An error occurred during rescheduling: {e}", "error")
        return redirect(url_for('reschedule_session', session_id=session_id))

@app.route('/admin')
def admin_dashboard():
    if 'user_id' not in session or session.get('user_role') != 'admin':
        flash('You are not authorized to view this page.', 'error')
        return redirect(url_for('signin'))

    pending_practitioners = []
    docs = practitioners_ref.where('verification_status', '==', 'Pending Review').stream()
    for doc in docs:
        practitioner_data = doc.to_dict()
        practitioner_data['doc_id'] = doc.id
        pending_practitioners.append(practitioner_data)
    
    return render_template('admin.html', practitioners=pending_practitioners)

@app.route('/admin/approve/<practitioner_id>', methods=['POST'])
def approve_practitioner(practitioner_id):
    if 'user_id' not in session or session.get('user_role') != 'admin':
        return redirect(url_for('signin'))
    
    try:
        practitioners_ref.document(practitioner_id).update({
            'verification_status': 'Verified'
        })
        flash('Practitioner approved successfully!', 'success')
    except Exception as e:
        flash(f'Error approving practitioner: {e}', 'error')
        
    return redirect(url_for('admin_dashboard'))

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')

@app.route('/therapists')
def therapists():
    if not db:
        return "Database is not available.", 500
    try:
        all_practitioners = practitioners_ref.stream()
        therapists_data = [{'doc_id': doc.id, **doc.to_dict()} for doc in all_practitioners]
        
        therapists_data.sort(key=lambda x: x.get('verification_status') != 'Verified')
        
        return render_template('therapists.html', therapists=therapists_data)
    except Exception as e:
        return f"An error occurred: {str(e)}", 500

# NEW ROUTE TO UPDATE TASK STATUS
@app.route('/update_task_status', methods=['POST'])
def update_task_status():
    if 'user_id' not in session or session.get('user_role') != 'patient':
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    data = request.json
    journey_id = data.get('journey_id')
    task_index = data.get('task_index')

    if journey_id is None or task_index is None:
        return jsonify({"success": False, "error": "Missing data"}), 400

    try:
        journey_ref = db.collection('patient_journeys').document(journey_id)
        journey_doc = journey_ref.get()

        if not journey_doc.exists:
            return jsonify({"success": False, "error": "Journey not found"}), 404

        # Security check to ensure the user owns this journey
        journey_data = journey_doc.to_dict()
        if journey_data.get('patient_uid') != session['user_id']:
            return jsonify({"success": False, "error": "Forbidden"}), 403

        tasks = journey_data.get('tasks', [])
        if 0 <= task_index < len(tasks):
            tasks[task_index]['status'] = 'completed'
            journey_ref.update({'tasks': tasks})
            return jsonify({"success": True, "message": "Task updated"})
        else:
            return jsonify({"success": False, "error": "Invalid task index"}), 400

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')