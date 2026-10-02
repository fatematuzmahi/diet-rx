-- 1. Table: patient
CREATE TABLE patient (
    patient_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR2(100) NOT NULL,
    email VARCHAR2(100) NOT NULL UNIQUE,
    password VARCHAR2(255) NOT NULL,
    age NUMBER(3),
    gender VARCHAR2(20),
    weight NUMBER(5,2),
    height NUMBER(5,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reset_token VARCHAR2(255)
);

-- 2. Table: doctor
CREATE TABLE doctor (
    doctor_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR2(100) NOT NULL,
    specialization VARCHAR2(100),
    phone VARCHAR2(20) UNIQUE
);

-- 3. Table: health_profile
CREATE TABLE health_profile (
    profile_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id NUMBER NOT NULL UNIQUE,
    conditions VARCHAR2(255),
    allergies VARCHAR2(255),
    activity_level VARCHAR2(50),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_health_profile_patient FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE
);

-- 4. Table: prescription
CREATE TABLE prescription (
    prescription_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id NUMBER NOT NULL,
    doctor_id NUMBER NOT NULL,
    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    restrictions CLOB,
    validated NUMBER(1) DEFAULT 0 CHECK (validated IN (0, 1)),
    CONSTRAINT fk_prescription_patient FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    CONSTRAINT fk_prescription_doctor FOREIGN KEY (doctor_id) REFERENCES doctor(doctor_id) ON DELETE CASCADE
);

-- 5. Table: diet_plan
CREATE TABLE diet_plan (
    plan_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id NUMBER NOT NULL,
    prescription_id NUMBER,
    calorie_target NUMBER,
    meal_schedule VARCHAR2(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_diet_plan_patient FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    CONSTRAINT fk_diet_plan_prescription FOREIGN KEY (prescription_id) REFERENCES prescription(prescription_id) ON DELETE SET NULL
);

-- 6. Table: budget
CREATE TABLE budget (
    budget_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id NUMBER NOT NULL,
    monthly_budget NUMBER(10,2),
    preferred_market VARCHAR2(100),
    CONSTRAINT fk_budget_patient FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE
);

-- 7. Table: activity_plan
CREATE TABLE activity_plan (
    activity_plan_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id NUMBER NOT NULL,
    plan_id NUMBER NOT NULL,
    exercise_list VARCHAR2(255),
    duration NUMBER,
    intensity VARCHAR2(50),
    CONSTRAINT fk_activity_plan_patient FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    CONSTRAINT fk_activity_plan_diet FOREIGN KEY (plan_id) REFERENCES diet_plan(plan_id) ON DELETE CASCADE
);

-- 8. Table: grocery_list
CREATE TABLE grocery_list (
    list_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id NUMBER NOT NULL,
    plan_id NUMBER NOT NULL,
    budget_id NUMBER,
    items CLOB,
    quantity VARCHAR2(100),
    total_cost NUMBER(10,2),
    CONSTRAINT fk_grocery_list_patient FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE,
    CONSTRAINT fk_grocery_list_plan FOREIGN KEY (plan_id) REFERENCES diet_plan(plan_id) ON DELETE CASCADE,
    CONSTRAINT fk_grocery_list_budget FOREIGN KEY (budget_id) REFERENCES budget(budget_id) ON DELETE SET NULL
);

-- 9. Table: notification
CREATE TABLE notification (
    notification_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id NUMBER NOT NULL,
    reminder_type VARCHAR2(50),
    schedule_time TIMESTAMP,
    alert_status NUMBER(1) DEFAULT 0 CHECK (alert_status IN (0, 1)),
    CONSTRAINT fk_notification_patient FOREIGN KEY (patient_id) REFERENCES patient(patient_id) ON DELETE CASCADE
);

ALTER TABLE patient ADD (
    is_verified NUMBER(1) DEFAULT 0 NOT NULL,
    verification_code_hash VARCHAR2(255),
    verification_expires_at TIMESTAMP,
    verified_at TIMESTAMP
);
-- Account Verification Fields
ALTER TABLE patient ADD (
    verification_code VARCHAR2(10),
    verification_code_expires_at TIMESTAMP,
    is_verified NUMBER(1) DEFAULT 0 NOT NULL
);