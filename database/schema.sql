-- PostgreSQL schema derived from current Django models.py files
-- Source apps: users, academics, placements
-- Notes:
-- 1) Table names match Django db_table (or default app_label_model).
-- 2) Foreign key columns use Django model column names.
-- 3) Includes all model-backed and auto-created M2M tables.

-- =========================================================
-- Core Academic Tables
-- =========================================================

-- Academic programs offered by the university.
CREATE TABLE program (
    id              SERIAL PRIMARY KEY,
    degree          VARCHAR(50) NOT NULL,
    branch          VARCHAR(50) NOT NULL,
    duration_years  SMALLINT NOT NULL,
    UNIQUE (degree, branch)
);

-- Academic departments (referenced by faculty).
CREATE TABLE department (
    department_id    SERIAL PRIMARY KEY,
    department_name  VARCHAR(150) NOT NULL UNIQUE
);

-- User profile linked to Django auth user.
CREATE TABLE users_user (
    user_id        SERIAL PRIMARY KEY,
    auth_user_id   INTEGER NOT NULL UNIQUE,
    name           VARCHAR(150) NOT NULL,
    email          VARCHAR(254) UNIQUE,
    phone          VARCHAR(50) NOT NULL,
    dob            DATE NOT NULL,
    gender         VARCHAR(20) NOT NULL,
    user_type      VARCHAR(50) NOT NULL,
    FOREIGN KEY (auth_user_id) REFERENCES auth_user (id) ON DELETE CASCADE
);

-- Student entity and academic standing.
CREATE TABLE users_student (
    user_id            INTEGER PRIMARY KEY,
    roll_number        VARCHAR(20) NOT NULL UNIQUE,
    admission_year     SMALLINT NOT NULL,
    program_id         INTEGER NOT NULL,
    current_semester   SMALLINT NOT NULL,
    academic_status    VARCHAR(50) NOT NULL,
    -- curr_cpi is a derived value updated by application logic / stored procedure.
    -- Do not compute and persist curr_cpi directly through ad-hoc DB queries.
    curr_cpi           NUMERIC(4,2) NOT NULL,
    backlog_count      SMALLINT NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users_user (user_id) ON DELETE CASCADE,
    FOREIGN KEY (program_id) REFERENCES program (id) ON DELETE RESTRICT
);

-- Faculty entity and departmental mapping.
CREATE TABLE users_faculty (
    user_id         INTEGER PRIMARY KEY,
    faculty_id      VARCHAR(20) NOT NULL UNIQUE,
    designation     VARCHAR(100) NOT NULL,
    department_id   INTEGER NOT NULL,
    experience      NUMERIC(4,1) NOT NULL,
    qualification   VARCHAR(255) NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users_user (user_id) ON DELETE CASCADE,
    FOREIGN KEY (department_id) REFERENCES department (department_id) ON DELETE RESTRICT
);

-- Admin users and roles.
CREATE TABLE users_admin (
    user_id     INTEGER PRIMARY KEY,
    admin_id    VARCHAR(20) NOT NULL UNIQUE,
    role        VARCHAR(100) NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users_user (user_id) ON DELETE CASCADE
);

-- Master course catalog.
CREATE TABLE course (
    course_code         VARCHAR(20) PRIMARY KEY,
    course_name         VARCHAR(200) NOT NULL,
    credits             SMALLINT NOT NULL,
    category            VARCHAR(10) NOT NULL,
    min_attendance_req  SMALLINT NOT NULL,
    program_id          INTEGER NOT NULL,
    FOREIGN KEY (program_id) REFERENCES program (id) ON DELETE RESTRICT
);

-- Per-term offerings of courses.
CREATE TABLE course_offering (
    offering_id             SERIAL PRIMARY KEY,
    course_code             VARCHAR(20) NOT NULL,
    academic_year           VARCHAR(20) NOT NULL,
    semester_no             SMALLINT NOT NULL,
    max_capacity            INTEGER NOT NULL DEFAULT 60,
    is_grading_finalized    BOOLEAN NOT NULL DEFAULT FALSE,
    FOREIGN KEY (course_code) REFERENCES course (course_code) ON DELETE CASCADE
);

-- Grade scale map (letter -> points).
CREATE TABLE grade_scale (
    grade_letter  VARCHAR(5) PRIMARY KEY,
    grade_point   NUMERIC(4,2) NOT NULL
);

-- Prerequisite relationship between courses.
CREATE TABLE prerequisite (
    course_id          VARCHAR(20) NOT NULL,
    prereq_course_id   VARCHAR(20) NOT NULL,
    min_grade_req_id   VARCHAR(5) NOT NULL,
    PRIMARY KEY (course_id, prereq_course_id),
    FOREIGN KEY (course_id) REFERENCES course (course_code) ON DELETE CASCADE,
    FOREIGN KEY (prereq_course_id) REFERENCES course (course_code) ON DELETE CASCADE,
    FOREIGN KEY (min_grade_req_id) REFERENCES grade_scale (grade_letter) ON DELETE RESTRICT
);

-- Student enrollment in a course offering, with attempt tracking.
CREATE TABLE enrollment (
    enrollment_id     SERIAL PRIMARY KEY,
    student_id        INTEGER NOT NULL,
    offering_id       INTEGER NOT NULL,
    faculty_id        INTEGER,
    attempt_no        SMALLINT NOT NULL,
    enrollment_type   VARCHAR(50) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'ONGOING',
    failure_reason    VARCHAR(50),
    FOREIGN KEY (student_id) REFERENCES users_student (user_id) ON DELETE CASCADE,
    FOREIGN KEY (offering_id) REFERENCES course_offering (offering_id) ON DELETE CASCADE,
    FOREIGN KEY (faculty_id) REFERENCES users_faculty (user_id) ON DELETE RESTRICT,
    CONSTRAINT unique_student_course_enrollment_attempt UNIQUE (student_id, offering_id, attempt_no),
    CONSTRAINT enrollment_status_failure_reason_ck CHECK (
        (status = 'PASS' AND failure_reason IS NULL)
        OR (status = 'BACKLOG' AND failure_reason IS NOT NULL)
        OR (status = 'ONGOING')
    )
);

-- Attendance events for a student's enrollment.
CREATE TABLE attendance (
    enrollment_id  INTEGER NOT NULL,
    date           DATE NOT NULL,
    status         VARCHAR(20) NOT NULL,
    FOREIGN KEY (enrollment_id) REFERENCES enrollment (enrollment_id) ON DELETE CASCADE,
    PRIMARY KEY (enrollment_id, date)
);

-- Assessment components configured per offering.
CREATE TABLE assessment_component (
    id          BIGSERIAL PRIMARY KEY,
    offering_id INTEGER NOT NULL,
    type        VARCHAR(50) NOT NULL,
    weightage   NUMERIC(5,2) NOT NULL,
    FOREIGN KEY (offering_id) REFERENCES course_offering (offering_id) ON DELETE CASCADE,
    UNIQUE (offering_id, type)
);

-- Marks scored by student per assessment component.
CREATE TABLE student_marks (
    id              BIGSERIAL PRIMARY KEY,
    enrollment_id   INTEGER NOT NULL,
    component_id    BIGINT NOT NULL,
    marks_obtained  NUMERIC(6,2) NOT NULL CHECK (marks_obtained >= 0),
    weighted_marks  NUMERIC(7,2) NOT NULL,
    FOREIGN KEY (enrollment_id) REFERENCES enrollment (enrollment_id) ON DELETE CASCADE,
    FOREIGN KEY (component_id) REFERENCES assessment_component (id) ON DELETE CASCADE,
    UNIQUE (enrollment_id, component_id)
);

-- Weighted marks are computed as: marks_obtained * component.weightage / 100.
CREATE OR REPLACE FUNCTION set_student_marks_weighted_value()
RETURNS TRIGGER AS $$
DECLARE
    component_weight NUMERIC(5,2);
BEGIN
    SELECT weightage INTO component_weight
    FROM assessment_component
    WHERE id = NEW.component_id;

    IF component_weight IS NULL THEN
        RAISE EXCEPTION 'Invalid component_id % for student_marks', NEW.component_id;
    END IF;

    IF NEW.marks_obtained < 0 THEN
        RAISE EXCEPTION 'marks_obtained cannot be negative';
    END IF;

    NEW.weighted_marks := ROUND((NEW.marks_obtained * component_weight) / 100.0, 2);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_student_marks_weighted_value ON student_marks;
CREATE TRIGGER trg_set_student_marks_weighted_value
BEFORE INSERT OR UPDATE ON student_marks
FOR EACH ROW
EXECUTE FUNCTION set_student_marks_weighted_value();

-- Final grade for an enrollment (one-to-one).
CREATE TABLE grade (
    enrollment_id       INTEGER PRIMARY KEY,
    grade_letter_id     VARCHAR(5) NOT NULL,
    is_counted_for_cpi  BOOLEAN NOT NULL,
    FOREIGN KEY (enrollment_id) REFERENCES enrollment (enrollment_id) ON DELETE CASCADE,
    FOREIGN KEY (grade_letter_id) REFERENCES grade_scale (grade_letter) ON DELETE RESTRICT
);

-- Offering-specific grade thresholds.
CREATE TABLE course_grade_scale (
    id               BIGSERIAL PRIMARY KEY,
    offering_id      INTEGER NOT NULL,
    grade_letter_id  VARCHAR(5) NOT NULL,
    min_score        NUMERIC(5,2) NOT NULL,
    FOREIGN KEY (offering_id) REFERENCES course_offering (offering_id) ON DELETE CASCADE,
    FOREIGN KEY (grade_letter_id) REFERENCES grade_scale (grade_letter) ON DELETE RESTRICT,
    CONSTRAINT unique_offering_grade_letter UNIQUE (offering_id, grade_letter_id),
    CONSTRAINT unique_offering_min_score UNIQUE (offering_id, min_score)
);

-- Semester-wise SPI and earned credits for a student.
CREATE TABLE semester_result (
    id                  BIGSERIAL PRIMARY KEY,
    student_id          INTEGER NOT NULL,
    academic_year       VARCHAR(20) NOT NULL,
    semester_no         SMALLINT NOT NULL,
    spi                 NUMERIC(4,2) NOT NULL,
    credits_earned_sem  SMALLINT NOT NULL,
    FOREIGN KEY (student_id) REFERENCES users_student (user_id) ON DELETE CASCADE,
    UNIQUE (student_id, academic_year, semester_no)
);

-- =========================================================
-- Supporting / Workflow Tables
-- =========================================================

-- Temporary cart used during course registration.
CREATE TABLE course_cart (
    cart_id      SERIAL PRIMARY KEY,
    student_id   INTEGER NOT NULL,
    offering_id  INTEGER NOT NULL,
    added_at     TIMESTAMP WITH TIME ZONE NOT NULL,
    FOREIGN KEY (student_id) REFERENCES users_student (user_id) ON DELETE CASCADE,
    FOREIGN KEY (offering_id) REFERENCES course_offering (offering_id) ON DELETE CASCADE,
    CONSTRAINT unique_course_cart_item UNIQUE (student_id, offering_id)
);

-- Registration open/close window for a semester and year.
CREATE TABLE registration_window (
    id              BIGSERIAL PRIMARY KEY,
    semester_no     SMALLINT NOT NULL,
    academic_year   VARCHAR(20) NOT NULL,
    start_datetime  TIMESTAMP WITH TIME ZONE NOT NULL,
    end_datetime    TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT unique_registration_window UNIQUE (semester_no, academic_year),
    CONSTRAINT registration_window_time_range_ck CHECK (start_datetime < end_datetime)
);

-- Recruiting companies for placements.
CREATE TABLE placements_company (
    company_id      SERIAL PRIMARY KEY,
    company_name    VARCHAR(255) NOT NULL,
    contact         VARCHAR(50) NOT NULL,
    email           VARCHAR(254) NOT NULL UNIQUE,
    industry_type   VARCHAR(100) NOT NULL
);

-- Placement opportunities from companies.
CREATE TABLE placements_placementoffer (
    id                    BIGSERIAL PRIMARY KEY,
    company_id            INTEGER NOT NULL,
    role_name             VARCHAR(255) NOT NULL,
    package_ctc           NUMERIC(10,2) NOT NULL,
    offer_type            VARCHAR(50),
    min_cpi               NUMERIC(4,2) NOT NULL DEFAULT 0,
    max_backlogs          SMALLINT NOT NULL DEFAULT 0,
    min_semester          SMALLINT NOT NULL DEFAULT 1,
    max_semester          SMALLINT NOT NULL DEFAULT 8,
    application_deadline  TIMESTAMP WITH TIME ZONE NOT NULL,
    FOREIGN KEY (company_id) REFERENCES placements_company (company_id) ON DELETE CASCADE
);

-- Auto-created M2M bridge: allowed programs per placement offer.
CREATE TABLE placements_placementoffer_allowed_programs (
    id                 BIGSERIAL PRIMARY KEY,
    placementoffer_id  BIGINT NOT NULL,
    program_id         INTEGER NOT NULL,
    FOREIGN KEY (placementoffer_id) REFERENCES placements_placementoffer (id) ON DELETE CASCADE,
    FOREIGN KEY (program_id) REFERENCES program (id) ON DELETE CASCADE,
    UNIQUE (placementoffer_id, program_id)
);

-- Student applications to placement offers.
CREATE TABLE placements_application (
    id                  BIGSERIAL PRIMARY KEY,
    student_id          INTEGER NOT NULL,
    placement_offer_id  BIGINT NOT NULL,
    status              VARCHAR(20) NOT NULL,
    applied_at          TIMESTAMP WITH TIME ZONE NOT NULL,
    FOREIGN KEY (student_id) REFERENCES users_student (user_id) ON DELETE CASCADE,
    FOREIGN KEY (placement_offer_id) REFERENCES placements_placementoffer (id) ON DELETE CASCADE,
    UNIQUE (student_id, placement_offer_id)
);

-- Final placement outcome snapshot per student.
CREATE TABLE placements_finaloutcome (
    student_id             INTEGER PRIMARY KEY,
    graduating_cpi         NUMERIC(4,2) NOT NULL,
    total_credits_earned   INTEGER NOT NULL,
    class_awarded          VARCHAR(50) NOT NULL,
    degree_awarded         VARCHAR(255) NOT NULL,
    graduation_year        SMALLINT NOT NULL,
    FOREIGN KEY (student_id) REFERENCES users_student (user_id) ON DELETE CASCADE
);

-- =========================================================
-- Explicit Indexes (from Meta.indexes and FK-heavy access paths)
-- =========================================================

CREATE INDEX users_student_program_id_idx ON users_student (program_id);
CREATE INDEX users_faculty_department_id_idx ON users_faculty (department_id);
CREATE INDEX course_program_id_idx ON course (program_id);
CREATE INDEX course_offering_course_code_idx ON course_offering (course_code);

CREATE INDEX prerequisite_course_id_idx ON prerequisite (course_id);
CREATE INDEX prerequisite_prereq_course_id_idx ON prerequisite (prereq_course_id);
CREATE INDEX prerequisite_min_grade_req_id_idx ON prerequisite (min_grade_req_id);

CREATE INDEX enrollment_student_idx ON enrollment (student_id);
CREATE INDEX enrollment_offering_idx ON enrollment (offering_id);
CREATE INDEX enrollment_faculty_id_idx ON enrollment (faculty_id);

CREATE INDEX attendance_status_idx ON attendance (status);

CREATE INDEX assessment_component_offering_id_idx ON assessment_component (offering_id);

CREATE INDEX student_marks_enrollment_id_idx ON student_marks (enrollment_id);
CREATE INDEX student_marks_component_id_idx ON student_marks (component_id);
CREATE INDEX studentmarks_enrollment_idx ON student_marks (enrollment_id);

CREATE INDEX grade_grade_letter_id_idx ON grade (grade_letter_id);

CREATE INDEX course_grade_scale_offering_id_idx ON course_grade_scale (offering_id);
CREATE INDEX course_grade_scale_grade_letter_id_idx ON course_grade_scale (grade_letter_id);

CREATE INDEX semester_result_student_id_idx ON semester_result (student_id);

CREATE INDEX course_cart_student_id_idx ON course_cart (student_id);
CREATE INDEX course_cart_offering_id_idx ON course_cart (offering_id);

CREATE INDEX placement_offer_company_idx ON placements_placementoffer (company_id);
CREATE INDEX placements_placementoffer_allowed_programs_placementoffer_id_idx ON placements_placementoffer_allowed_programs (placementoffer_id);
CREATE INDEX placements_placementoffer_allowed_programs_program_id_idx ON placements_placementoffer_allowed_programs (program_id);

CREATE INDEX application_student_idx ON placements_application (student_id);
CREATE INDEX application_offer_idx ON placements_application (placement_offer_id);
CREATE UNIQUE INDEX unique_accepted_offer_per_student
ON placements_application (student_id)
WHERE status = 'Accepted';

CREATE INDEX final_outcome_student_idx ON placements_finaloutcome (student_id);

-- Transaction-safe placement offer acceptance.
-- Row-level lock on users_student serializes concurrent accept attempts per student.
CREATE OR REPLACE PROCEDURE accept_offer(p_student_id INT, p_offer_id INT)
LANGUAGE plpgsql
AS $$
DECLARE
        accepted_offer_id BIGINT;
        updated_rows INT;
BEGIN
        -- Lock the student row to prevent concurrent acceptance races.
        PERFORM 1
        FROM users_student
        WHERE user_id = p_student_id
        FOR UPDATE;

        IF NOT FOUND THEN
                RAISE EXCEPTION 'Student % not found', p_student_id;
        END IF;

        SELECT placement_offer_id
        INTO accepted_offer_id
        FROM placements_application
        WHERE student_id = p_student_id
            AND status = 'Accepted'
        LIMIT 1;

        IF accepted_offer_id IS NOT NULL THEN
                RAISE EXCEPTION 'Offer already accepted';
        END IF;

        UPDATE placements_application
        SET status = 'Accepted'
        WHERE student_id = p_student_id
            AND placement_offer_id = p_offer_id;

        GET DIAGNOSTICS updated_rows = ROW_COUNT;
        IF updated_rows = 0 THEN
                RAISE EXCEPTION 'No application found for student % and offer %', p_student_id, p_offer_id;
        END IF;

        UPDATE placements_application
        SET status = 'Rejected'
        WHERE student_id = p_student_id
            AND placement_offer_id <> p_offer_id;
END;
$$;
