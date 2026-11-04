CREATE TYPE bugsourcetype AS ENUM ('REQUIREMENT', 'CASE', 'LEGACY_BUG', 'MANUAL', 'RETEST', 'FIELD_TEST')

-- statement-break

CREATE TYPE testresultstatus AS ENUM ('PASSED', 'FAILED', 'BLOCKED', 'PARTIAL', 'UNTESTED')

-- statement-break

CREATE TYPE feedbackstatus AS ENUM ('PENDING', 'PROCESSING', 'RESOLVED', 'CLOSED')

-- statement-break

CREATE TYPE fieldtestpurposetype AS ENUM ('REQUIREMENT', 'FEATURE')

-- statement-break

CREATE TYPE fieldtestresultstatus AS ENUM ('PASSED', 'FAILED')

-- statement-break

CREATE TYPE assessmentstatus AS ENUM ('DRAFTING', 'ANSWERING', 'GRADING', 'PUBLISHED')

-- statement-break

CREATE TYPE questiontype AS ENUM ('SINGLE', 'MULTI', 'JUDGE', 'BLANK', 'SHORT')

-- statement-break

CREATE TYPE submissionstatus AS ENUM ('SUBMITTED', 'GRADED')

-- statement-break

CREATE TYPE learning_content_kind AS ENUM ('MATERIAL', 'COURSE')

-- statement-break

CREATE TYPE learning_quiz_status AS ENUM ('DRAFT', 'OPEN', 'CLOSED')

-- statement-break

CREATE TYPE learning_question_type AS ENUM ('SINGLE_CHOICE', 'MULTIPLE_CHOICE', 'FILL_BLANK', 'SHORT_ANSWER')

-- statement-break

CREATE TYPE learning_fill_grading_mode AS ENUM ('CONTAINS', 'EXACT', 'NORMALIZED', 'MATCH_ANY', 'MATCH_COUNT')

-- statement-break

CREATE TYPE requirementstatus AS ENUM ('PENDING', 'ASSIGNED', 'CASE_DONE', 'TESTING', 'TEST_DONE', 'RETEST_PENDING', 'RETEST_DONE')

-- statement-break

CREATE TYPE taskboardstatus AS ENUM ('TODO', 'IN_PROGRESS', 'BLOCKED', 'DONE', 'DEFERRED')

-- statement-break

CREATE TYPE taskboardpriority AS ENUM ('LOW', 'NORMAL', 'HIGH', 'URGENT')

-- statement-break

CREATE TYPE userrole AS ENUM ('ADMIN', 'USER')

-- statement-break

CREATE TYPE versiontype AS ENUM ('MAJOR', 'MINOR')

-- statement-break

CREATE TABLE audit_logs (
	id SERIAL NOT NULL,
	actor_id INTEGER,
	action VARCHAR(64) NOT NULL,
	target_type VARCHAR(64) NOT NULL,
	target_id VARCHAR(64),
	detail TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

-- statement-break

CREATE INDEX ix_audit_logs_target_id ON audit_logs (target_id)

-- statement-break

CREATE INDEX ix_audit_logs_id ON audit_logs (id)

-- statement-break

CREATE INDEX ix_audit_logs_actor_id ON audit_logs (actor_id)

-- statement-break

CREATE INDEX ix_audit_logs_action ON audit_logs (action)

-- statement-break

CREATE INDEX ix_audit_logs_target_type ON audit_logs (target_type)

-- statement-break

CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at)

-- statement-break

CREATE TABLE build_records (
	id SERIAL NOT NULL,
	job_name VARCHAR(120) NOT NULL,
	build_number VARCHAR(64) NOT NULL,
	build_status VARCHAR(32) NOT NULL,
	version_name VARCHAR(255),
	branch VARCHAR(255),
	build_url VARCHAR(500),
	change_log TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	auto_archive_status VARCHAR(30),
	auto_archive_minor_version_id INTEGER,
	auto_archive_message VARCHAR(500),
	zentao_push_status VARCHAR(30),
	zentao_push_message VARCHAR(500),
	zentao_pushed_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_build_records_job_build UNIQUE (job_name, build_number)
)

-- statement-break

CREATE INDEX ix_build_records_id ON build_records (id)

-- statement-break

CREATE INDEX ix_build_records_created_at ON build_records (created_at)

-- statement-break

CREATE INDEX ix_build_records_updated_at ON build_records (updated_at)

-- statement-break

CREATE INDEX ix_build_records_job_name ON build_records (job_name)

-- statement-break

CREATE INDEX ix_build_records_build_status ON build_records (build_status)

-- statement-break

CREATE INDEX ix_build_records_build_number ON build_records (build_number)

-- statement-break

CREATE TABLE competitor_analysis_reports (
	report_id VARCHAR(80) NOT NULL,
	data_json TEXT NOT NULL,
	version INTEGER NOT NULL,
	updated_by_id INTEGER,
	updated_by_name VARCHAR(120),
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (report_id)
)

-- statement-break

CREATE TABLE database_compare_inputs (
	id VARCHAR(32) NOT NULL,
	owner_id INTEGER NOT NULL,
	manifest_json TEXT NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

-- statement-break

CREATE INDEX ix_database_compare_inputs_owner_id ON database_compare_inputs (owner_id)

-- statement-break

CREATE TABLE database_compare_tasks (
	id VARCHAR(32) NOT NULL,
	owner_id INTEGER NOT NULL,
	old_input_id VARCHAR(32) NOT NULL,
	new_input_id VARCHAR(32) NOT NULL,
	old_label VARCHAR(120) NOT NULL,
	new_label VARCHAR(120) NOT NULL,
	pairs_json TEXT NOT NULL,
	rules_json TEXT NOT NULL,
	status VARCHAR(24) NOT NULL,
	cancel_requested BOOLEAN NOT NULL,
	progress_json TEXT NOT NULL,
	summary_json TEXT NOT NULL,
	error TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id)
)

-- statement-break

CREATE INDEX ix_database_compare_tasks_owner_id ON database_compare_tasks (owner_id)

-- statement-break

CREATE INDEX ix_database_compare_tasks_status ON database_compare_tasks (status)

-- statement-break

CREATE TABLE feature_tree_nodes (
	id SERIAL NOT NULL,
	software_id INTEGER NOT NULL,
	parent_id INTEGER,
	name VARCHAR(200) NOT NULL,
	is_root BOOLEAN NOT NULL,
	note_html TEXT,
	note_updated_by INTEGER,
	note_updated_at TIMESTAMP WITHOUT TIME ZONE,
	sort_order INTEGER NOT NULL,
	created_by INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(parent_id) REFERENCES feature_tree_nodes (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_feature_tree_nodes_parent_id ON feature_tree_nodes (parent_id)

-- statement-break

CREATE INDEX ix_feature_tree_nodes_software_id ON feature_tree_nodes (software_id)

-- statement-break

CREATE INDEX ix_feature_tree_nodes_id ON feature_tree_nodes (id)

-- statement-break

CREATE TABLE holidays (
	day DATE NOT NULL,
	is_off BOOLEAN NOT NULL,
	name VARCHAR(80),
	year INTEGER,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (day)
)

-- statement-break

CREATE INDEX ix_holidays_year ON holidays (year)

-- statement-break

CREATE TABLE software_products (
	id SERIAL NOT NULL,
	name VARCHAR(120) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	zentao_product_id INTEGER,
	zentao_product_name_cache VARCHAR(255),
	PRIMARY KEY (id)
)

-- statement-break

CREATE INDEX ix_software_products_zentao_product_id ON software_products (zentao_product_id)

-- statement-break

CREATE INDEX ix_software_products_id ON software_products (id)

-- statement-break

CREATE UNIQUE INDEX ix_software_products_name ON software_products (name)

-- statement-break

CREATE TABLE story_ai_results (
	id SERIAL NOT NULL,
	batch_id VARCHAR(36) NOT NULL,
	story_id INTEGER NOT NULL,
	execution_id INTEGER,
	execution_name VARCHAR(255),
	title VARCHAR(500),
	briefing TEXT,
	module_name VARCHAR(255),
	scene_name VARCHAR(255),
	stage_name VARCHAR(255),
	case_type VARCHAR(64),
	priority VARCHAR(32),
	precondition TEXT,
	steps_json TEXT,
	keywords VARCHAR(500),
	risk_points_json TEXT,
	questions_to_confirm_json TEXT,
	testcase_template TEXT,
	raw_ai_result_json TEXT,
	ai_status VARCHAR(16) NOT NULL,
	ai_error_message TEXT,
	created_by INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

-- statement-break

CREATE INDEX ix_story_ai_results_story_id ON story_ai_results (story_id)

-- statement-break

CREATE INDEX ix_story_ai_results_created_by ON story_ai_results (created_by)

-- statement-break

CREATE INDEX ix_story_ai_results_ai_status ON story_ai_results (ai_status)

-- statement-break

CREATE INDEX ix_story_ai_results_execution_id ON story_ai_results (execution_id)

-- statement-break

CREATE INDEX ix_story_ai_results_id ON story_ai_results (id)

-- statement-break

CREATE INDEX ix_story_ai_results_batch ON story_ai_results (batch_id)

-- statement-break

CREATE INDEX ix_story_ai_results_batch_id ON story_ai_results (batch_id)

-- statement-break

CREATE INDEX ix_story_ai_results_story_created ON story_ai_results (story_id, created_at)

-- statement-break

CREATE INDEX ix_story_ai_results_created_at ON story_ai_results (created_at)

-- statement-break

CREATE TABLE sync_locks (
	key VARCHAR(120) NOT NULL,
	holder VARCHAR(120),
	acquired_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	ttl_seconds INTEGER NOT NULL,
	PRIMARY KEY (key)
)

-- statement-break

CREATE TABLE terminal_devices (
	id SERIAL NOT NULL,
	serial VARCHAR(120) NOT NULL,
	name VARCHAR(120) NOT NULL,
	platform VARCHAR(20) NOT NULL,
	status VARCHAR(20) NOT NULL,
	connection VARCHAR(20),
	model VARCHAR(120),
	os_version VARCHAR(40),
	enabled BOOLEAN NOT NULL,
	last_seen_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

-- statement-break

CREATE INDEX ix_terminal_devices_id ON terminal_devices (id)

-- statement-break

CREATE UNIQUE INDEX ix_terminal_devices_serial ON terminal_devices (serial)

-- statement-break

CREATE TABLE users (
	id SERIAL NOT NULL,
	username VARCHAR(50) NOT NULL,
	display_name VARCHAR(80),
	password_hash VARCHAR(255) NOT NULL,
	role userrole NOT NULL,
	is_team_member BOOLEAN NOT NULL,
	tab_permissions TEXT,
	account_type VARCHAR(20) NOT NULL,
	is_active BOOLEAN NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	session_token VARCHAR(36),
	session_token_mobile VARCHAR(36),
	zentao_account VARCHAR(120),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

-- statement-break

CREATE INDEX ix_users_id ON users (id)

-- statement-break

CREATE UNIQUE INDEX ix_users_username ON users (username)

-- statement-break

CREATE TABLE versions (
	id SERIAL NOT NULL,
	version_no VARCHAR(128) NOT NULL,
	version_type versiontype NOT NULL,
	parent_id INTEGER,
	software_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	final_test_enabled BOOLEAN NOT NULL,
	final_test_started_at TIMESTAMP WITHOUT TIME ZONE,
	zentao_project_id INTEGER,
	zentao_project_name_cache VARCHAR(255),
	zentao_execution_id INTEGER,
	zentao_execution_name_cache VARCHAR(255),
	zentao_build_id INTEGER,
	zentao_build_name_cache VARCHAR(255),
	zentao_testtask_id INTEGER,
	zentao_testtask_name_cache VARCHAR(255),
	zentao_release_id INTEGER,
	zentao_release_name_cache VARCHAR(255),
	PRIMARY KEY (id),
	CONSTRAINT uq_version_software_no_type UNIQUE (software_id, version_no, version_type),
	FOREIGN KEY(parent_id) REFERENCES versions (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_versions_version_no ON versions (version_no)

-- statement-break

CREATE INDEX ix_versions_zentao_release_id ON versions (zentao_release_id)

-- statement-break

CREATE INDEX ix_versions_id ON versions (id)

-- statement-break

CREATE INDEX ix_versions_zentao_build_id ON versions (zentao_build_id)

-- statement-break

CREATE INDEX ix_versions_zentao_project_id ON versions (zentao_project_id)

-- statement-break

CREATE INDEX ix_versions_zentao_testtask_id ON versions (zentao_testtask_id)

-- statement-break

CREATE INDEX ix_versions_software_id ON versions (software_id)

-- statement-break

CREATE INDEX ix_versions_zentao_execution_id ON versions (zentao_execution_id)

-- statement-break

CREATE TABLE zentao_task_mirror (
	task_id SERIAL NOT NULL,
	execution_id INTEGER NOT NULL,
	execution_name_cache VARCHAR(255),
	project_id INTEGER,
	parent INTEGER NOT NULL,
	is_parent INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	type VARCHAR(40),
	status VARCHAR(40),
	pri INTEGER,
	story INTEGER,
	task_story_change_pending BOOLEAN,
	task_story_version INTEGER,
	task_latest_story_version INTEGER,
	task_change_checked_at TIMESTAMP WITHOUT TIME ZONE,
	assigned_to VARCHAR(120),
	assigned_to_realname VARCHAR(120),
	assignee_user_id INTEGER,
	finished_by VARCHAR(120),
	finished_by_realname VARCHAR(120),
	estimate FLOAT,
	consumed FLOAT,
	"left" FLOAT,
	est_started DATE,
	deadline DATE,
	real_started TIMESTAMP WITHOUT TIME ZONE,
	finished_date TIMESTAMP WITHOUT TIME ZONE,
	"desc" TEXT,
	local_started_at TIMESTAMP WITHOUT TIME ZONE,
	consumed_accum FLOAT NOT NULL,
	efforts_submitted FLOAT NOT NULL,
	synced_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (task_id)
)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_exec ON zentao_task_mirror (execution_id)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_assigned_to ON zentao_task_mirror (assigned_to)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_story ON zentao_task_mirror (story)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_dates ON zentao_task_mirror (est_started, deadline)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_task_story_change_pending ON zentao_task_mirror (task_story_change_pending)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_assignee_user ON zentao_task_mirror (assignee_user_id)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_assignee_user_id ON zentao_task_mirror (assignee_user_id)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_execution_id ON zentao_task_mirror (execution_id)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_task_id ON zentao_task_mirror (task_id)

-- statement-break

CREATE INDEX ix_zentao_task_mirror_status ON zentao_task_mirror (status)

-- statement-break

CREATE TABLE zentao_testcase_mirror (
	id SERIAL NOT NULL,
	zentao_case_id VARCHAR(30) NOT NULL,
	zentao_case_numeric_id INTEGER NOT NULL,
	zentao_product_id INTEGER,
	zentao_product_name VARCHAR(255),
	zentao_module_id INTEGER,
	zentao_module_name VARCHAR(255),
	zentao_story_id INTEGER,
	zentao_execution_id INTEGER,
	title TEXT,
	case_type VARCHAR(40),
	stage VARCHAR(40),
	status VARCHAR(40),
	pri INTEGER,
	precondition TEXT,
	steps_digest TEXT,
	last_runner_account VARCHAR(100),
	last_runner_name VARCHAR(100),
	last_run_date TIMESTAMP WITHOUT TIME ZONE,
	last_run_result VARCHAR(40),
	bugs_count INTEGER,
	zentao_case_url TEXT,
	deleted BOOLEAN NOT NULL,
	remote_opened_at TIMESTAMP WITHOUT TIME ZONE,
	remote_updated_at TIMESTAMP WITHOUT TIME ZONE,
	last_zentao_synced_at TIMESTAMP WITHOUT TIME ZONE,
	sync_source VARCHAR(40),
	raw_payload TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_zentao_testcase_mirror_case_numeric_id UNIQUE (zentao_case_numeric_id)
)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_remote_updated_at ON zentao_testcase_mirror (remote_updated_at)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_zentao_case_numeric_id ON zentao_testcase_mirror (zentao_case_numeric_id)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_zentao_case_id ON zentao_testcase_mirror (zentao_case_id)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_zentao_story_id ON zentao_testcase_mirror (zentao_story_id)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_status ON zentao_testcase_mirror (status)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_last_zentao_synced_at ON zentao_testcase_mirror (last_zentao_synced_at)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_deleted ON zentao_testcase_mirror (deleted)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_zentao_execution_id ON zentao_testcase_mirror (zentao_execution_id)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_zentao_module_id ON zentao_testcase_mirror (zentao_module_id)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_id ON zentao_testcase_mirror (id)

-- statement-break

CREATE INDEX ix_zentao_testcase_mirror_zentao_product_id ON zentao_testcase_mirror (zentao_product_id)

-- statement-break

CREATE TABLE bugly_applications (
	id SERIAL NOT NULL,
	name VARCHAR(120) NOT NULL,
	source_host VARCHAR(255) NOT NULL,
	bugly_app_id VARCHAR(100),
	package_name VARCHAR(255),
	platform VARCHAR(20) NOT NULL,
	source_timezone VARCHAR(64) NOT NULL,
	software_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name),
	FOREIGN KEY(software_id) REFERENCES software_products (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE TABLE cad_boards (
	id SERIAL NOT NULL,
	name VARCHAR(200) NOT NULL,
	description TEXT,
	sort_order INTEGER NOT NULL,
	created_by_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(created_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_cad_boards_id ON cad_boards (id)

-- statement-break

CREATE TABLE feature_tree_marks (
	id SERIAL NOT NULL,
	node_id INTEGER NOT NULL,
	version_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	comment_html TEXT,
	is_auto BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_feature_mark_node_ver_user UNIQUE (node_id, version_id, user_id),
	FOREIGN KEY(node_id) REFERENCES feature_tree_nodes (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_feature_tree_marks_version_id ON feature_tree_marks (version_id)

-- statement-break

CREATE INDEX ix_feature_tree_marks_id ON feature_tree_marks (id)

-- statement-break

CREATE INDEX ix_feature_tree_marks_user_id ON feature_tree_marks (user_id)

-- statement-break

CREATE INDEX ix_feature_tree_marks_node_id ON feature_tree_marks (node_id)

-- statement-break

CREATE TABLE feature_tree_case_links (
	id SERIAL NOT NULL,
	node_id INTEGER NOT NULL,
	zentao_case_numeric_id INTEGER NOT NULL,
	created_by INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_feature_case_link_node_case UNIQUE (node_id, zentao_case_numeric_id),
	FOREIGN KEY(node_id) REFERENCES feature_tree_nodes (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_feature_tree_case_links_node_id ON feature_tree_case_links (node_id)

-- statement-break

CREATE INDEX ix_feature_tree_case_links_id ON feature_tree_case_links (id)

-- statement-break

CREATE INDEX ix_feature_tree_case_links_zentao_case_numeric_id ON feature_tree_case_links (zentao_case_numeric_id)

-- statement-break

CREATE TABLE feedback_records (
	id SERIAL NOT NULL,
	feedback_no VARCHAR(30),
	major_version_id INTEGER NOT NULL,
	minor_version_id INTEGER NOT NULL,
	summary TEXT NOT NULL,
	status feedbackstatus NOT NULL,
	creator_id INTEGER NOT NULL,
	assignee_id INTEGER,
	handling_result TEXT,
	handled_major_version_id INTEGER,
	handled_minor_version_id INTEGER,
	handled_by_id INTEGER,
	handled_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(major_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(minor_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(creator_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(assignee_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(handled_major_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(handled_minor_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(handled_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_feedback_records_handled_by_id ON feedback_records (handled_by_id)

-- statement-break

CREATE UNIQUE INDEX ix_feedback_records_feedback_no ON feedback_records (feedback_no)

-- statement-break

CREATE INDEX ix_feedback_records_major_version_id ON feedback_records (major_version_id)

-- statement-break

CREATE INDEX ix_feedback_records_assignee_id ON feedback_records (assignee_id)

-- statement-break

CREATE INDEX ix_feedback_records_status ON feedback_records (status)

-- statement-break

CREATE INDEX ix_feedback_records_id ON feedback_records (id)

-- statement-break

CREATE INDEX ix_feedback_records_creator_id ON feedback_records (creator_id)

-- statement-break

CREATE INDEX ix_feedback_records_minor_version_id ON feedback_records (minor_version_id)

-- statement-break

CREATE INDEX ix_feedback_records_created_at ON feedback_records (created_at)

-- statement-break

CREATE TABLE learning_topics (
	id SERIAL NOT NULL,
	title VARCHAR(200) NOT NULL,
	description TEXT,
	created_by_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(created_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_learning_topics_id ON learning_topics (id)

-- statement-break

CREATE INDEX ix_learning_topics_created_by_id ON learning_topics (created_by_id)

-- statement-break

CREATE INDEX ix_learning_topics_created_at ON learning_topics (created_at)

-- statement-break

CREATE TABLE learning_contents (
	id SERIAL NOT NULL,
	creator_id INTEGER NOT NULL,
	kind learning_content_kind NOT NULL,
	title VARCHAR(200) NOT NULL,
	description TEXT,
	body TEXT,
	resource_url VARCHAR(1000),
	published BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(creator_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_learning_contents_id ON learning_contents (id)

-- statement-break

CREATE INDEX ix_learning_contents_creator_id ON learning_contents (creator_id)

-- statement-break

CREATE TABLE learning_quizzes (
	id SERIAL NOT NULL,
	creator_id INTEGER NOT NULL,
	title VARCHAR(200) NOT NULL,
	description TEXT,
	status learning_quiz_status NOT NULL,
	results_released BOOLEAN NOT NULL,
	show_answers BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	closed_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(creator_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_learning_quizzes_status ON learning_quizzes (status)

-- statement-break

CREATE INDEX ix_learning_quizzes_id ON learning_quizzes (id)

-- statement-break

CREATE INDEX ix_learning_quizzes_creator_id ON learning_quizzes (creator_id)

-- statement-break

CREATE TABLE requirements (
	id SERIAL NOT NULL,
	zentao_req_id VARCHAR(20) NOT NULL,
	title VARCHAR(255) NOT NULL,
	major_version_id INTEGER NOT NULL,
	owner_id INTEGER,
	case_completed BOOLEAN NOT NULL,
	test_completed BOOLEAN NOT NULL,
	test_completed_at TIMESTAMP WITHOUT TIME ZONE,
	retest_completed BOOLEAN NOT NULL,
	retested_by_id INTEGER,
	retested_at TIMESTAMP WITHOUT TIME ZONE,
	retest_minor_version_id INTEGER,
	retest_passed BOOLEAN,
	test_notes TEXT,
	test_notes_html TEXT,
	test_notes_updated_at TIMESTAMP WITHOUT TIME ZONE,
	test_notes_updated_by_id INTEGER,
	status requirementstatus NOT NULL,
	zentao_story_id INTEGER,
	zentao_plan_id INTEGER,
	zentao_plan_title_cache VARCHAR(255),
	estimated_test_hours FLOAT NOT NULL,
	zentao_task_id INTEGER,
	zentao_parent_task_id INTEGER,
	task_started_at TIMESTAMP WITHOUT TIME ZONE,
	task_finished_at TIMESTAMP WITHOUT TIME ZONE,
	task_consumed_accum FLOAT NOT NULL,
	task_efforts_submitted FLOAT NOT NULL,
	zentao_task_status_cache VARCHAR(20),
	zentao_task_assigned_to VARCHAR(120),
	task_story_change_pending BOOLEAN,
	task_story_version INTEGER,
	task_latest_story_version INTEGER,
	task_change_checked_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_requirements_major_reqid UNIQUE (major_version_id, zentao_req_id),
	FOREIGN KEY(major_version_id) REFERENCES versions (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(owner_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(retested_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(retest_minor_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(test_notes_updated_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_requirements_zentao_req_id ON requirements (zentao_req_id)

-- statement-break

CREATE INDEX ix_requirements_task_story_change_pending ON requirements (task_story_change_pending)

-- statement-break

CREATE INDEX ix_requirements_zentao_task_id ON requirements (zentao_task_id)

-- statement-break

CREATE INDEX ix_requirements_test_completed_at ON requirements (test_completed_at)

-- statement-break

CREATE INDEX ix_requirements_major_test_completed ON requirements (major_version_id, test_completed)

-- statement-break

CREATE INDEX ix_requirements_zentao_story_id ON requirements (zentao_story_id)

-- statement-break

CREATE INDEX ix_requirements_id ON requirements (id)

-- statement-break

CREATE TABLE zentao_ai_case_workspaces (
	id VARCHAR(36) NOT NULL,
	user_id INTEGER NOT NULL,
	result_id INTEGER NOT NULL,
	base_url VARCHAR(255) NOT NULL,
	product_id INTEGER NOT NULL,
	story_id INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	context_json TEXT NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (user_id, result_id, base_url),
	FOREIGN KEY(user_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(result_id) REFERENCES story_ai_results (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_zentao_ai_case_workspaces_user_id ON zentao_ai_case_workspaces (user_id)

-- statement-break

CREATE INDEX ix_zentao_ai_case_workspaces_result_id ON zentao_ai_case_workspaces (result_id)

-- statement-break

CREATE TABLE task_board_tasks (
	id SERIAL NOT NULL,
	board_date DATE NOT NULL,
	title VARCHAR(200) NOT NULL,
	description TEXT,
	status taskboardstatus NOT NULL,
	priority taskboardpriority NOT NULL,
	assignee_id INTEGER,
	assigner_id INTEGER,
	created_by_id INTEGER,
	updated_by_id INTEGER,
	software_id INTEGER,
	major_version_id INTEGER,
	target_type VARCHAR(40),
	target_id INTEGER,
	target_label_cache VARCHAR(255),
	due_at TIMESTAMP WITHOUT TIME ZONE,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	completed_at TIMESTAMP WITHOUT TIME ZONE,
	deferred_from_date DATE,
	sort_order INTEGER NOT NULL,
	archived BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(assignee_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(assigner_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(created_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(updated_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(software_id) REFERENCES software_products (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(major_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_task_board_tasks_target ON task_board_tasks (target_type, target_id)

-- statement-break

CREATE INDEX ix_task_board_tasks_priority ON task_board_tasks (priority)

-- statement-break

CREATE INDEX ix_task_board_tasks_major_version_id ON task_board_tasks (major_version_id)

-- statement-break

CREATE INDEX ix_task_board_tasks_status ON task_board_tasks (status)

-- statement-break

CREATE INDEX ix_task_board_tasks_assignee_id ON task_board_tasks (assignee_id)

-- statement-break

CREATE INDEX ix_task_board_tasks_archived ON task_board_tasks (archived)

-- statement-break

CREATE INDEX ix_task_board_tasks_board_date ON task_board_tasks (board_date)

-- statement-break

CREATE INDEX ix_task_board_tasks_software_id ON task_board_tasks (software_id)

-- statement-break

CREATE INDEX ix_task_board_tasks_assignee_date ON task_board_tasks (assignee_id, board_date)

-- statement-break

CREATE INDEX ix_task_board_tasks_id ON task_board_tasks (id)

-- statement-break

CREATE INDEX ix_task_board_tasks_board_date_status ON task_board_tasks (board_date, status, sort_order)

-- statement-break

CREATE INDEX ix_task_board_tasks_created_at ON task_board_tasks (created_at)

-- statement-break

CREATE INDEX ix_task_board_tasks_software_date ON task_board_tasks (software_id, board_date)

-- statement-break

CREATE INDEX ix_task_board_tasks_major_date ON task_board_tasks (major_version_id, board_date)

-- statement-break

CREATE TABLE terminal_device_locks (
	id SERIAL NOT NULL,
	device_id INTEGER NOT NULL,
	lock_type VARCHAR(20) NOT NULL,
	holder_kind VARCHAR(20) NOT NULL,
	holder_user_id INTEGER,
	jenkins_job VARCHAR(255),
	jenkins_build VARCHAR(64),
	acquired_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	heartbeat_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	released_at TIMESTAMP WITHOUT TIME ZONE,
	release_reason VARCHAR(20),
	PRIMARY KEY (id),
	FOREIGN KEY(device_id) REFERENCES terminal_devices (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(holder_user_id) REFERENCES users (id) ON DELETE SET NULL DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_terminal_device_locks_device_id ON terminal_device_locks (device_id)

-- statement-break

CREATE INDEX ix_terminal_device_locks_holder_user_id ON terminal_device_locks (holder_user_id)

-- statement-break

CREATE INDEX ix_terminal_locks_device_active ON terminal_device_locks (device_id, released_at)

-- statement-break

CREATE INDEX ix_terminal_device_locks_id ON terminal_device_locks (id)

-- statement-break

CREATE UNIQUE INDEX uq_terminal_active_lock ON terminal_device_locks (device_id) WHERE released_at IS NULL

-- statement-break

CREATE TABLE terminal_stream_tickets (
	id SERIAL NOT NULL,
	token VARCHAR(64) NOT NULL,
	device_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	mode VARCHAR(20) NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	used_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(device_id) REFERENCES terminal_devices (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_terminal_stream_tickets_user_id ON terminal_stream_tickets (user_id)

-- statement-break

CREATE INDEX ix_terminal_stream_tickets_device_id ON terminal_stream_tickets (device_id)

-- statement-break

CREATE UNIQUE INDEX ix_terminal_stream_tickets_token ON terminal_stream_tickets (token)

-- statement-break

CREATE INDEX ix_terminal_stream_tickets_id ON terminal_stream_tickets (id)

-- statement-break

CREATE TABLE user_ai_provider_configs (
	id SERIAL NOT NULL,
	user_id INTEGER NOT NULL,
	provider_name VARCHAR(40) NOT NULL,
	api_key_encrypted TEXT,
	api_key_masked VARCHAR(120),
	is_enabled BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_user_ai_provider_user_provider UNIQUE (user_id, provider_name),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_user_ai_provider_configs_id ON user_ai_provider_configs (id)

-- statement-break

CREATE INDEX ix_user_ai_provider_configs_user_id ON user_ai_provider_configs (user_id)

-- statement-break

CREATE TABLE user_jenkins_bindings (
	id SERIAL NOT NULL,
	user_id INTEGER NOT NULL,
	base_url VARCHAR(500) NOT NULL,
	jenkins_account VARCHAR(120) NOT NULL,
	jenkins_token_ciphertext TEXT,
	last_check_at TIMESTAMP WITHOUT TIME ZONE,
	last_check_status VARCHAR(20),
	last_error_message TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_user_jenkins_bindings_user_id UNIQUE (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_user_jenkins_bindings_id ON user_jenkins_bindings (id)

-- statement-break

CREATE INDEX ix_user_jenkins_bindings_user_id ON user_jenkins_bindings (user_id)

-- statement-break

CREATE TABLE user_zentao_bindings (
	id SERIAL NOT NULL,
	user_id INTEGER NOT NULL,
	base_url VARCHAR(500) NOT NULL,
	zentao_account VARCHAR(120) NOT NULL,
	zentao_password_ciphertext TEXT,
	zentao_password_iv VARCHAR(64),
	token_value TEXT,
	token_expires_at TIMESTAMP WITHOUT TIME ZONE,
	last_refresh_at TIMESTAMP WITHOUT TIME ZONE,
	last_refresh_status VARCHAR(20),
	last_error_message TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_user_zentao_bindings_user_id UNIQUE (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_user_zentao_bindings_user_id ON user_zentao_bindings (user_id)

-- statement-break

CREATE INDEX ix_user_zentao_bindings_id ON user_zentao_bindings (id)

-- statement-break

CREATE TABLE automation_integration_credentials (
	id SERIAL NOT NULL,
	owner_id INTEGER NOT NULL,
	jenkins_base_url VARCHAR(500) NOT NULL,
	key_hash VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_automation_credential_owner_server UNIQUE (owner_id, jenkins_base_url),
	FOREIGN KEY(owner_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_automation_integration_credentials_owner_id ON automation_integration_credentials (owner_id)

-- statement-break

CREATE TABLE automation_repositories (
	id SERIAL NOT NULL,
	owner_id INTEGER NOT NULL,
	name VARCHAR(160) NOT NULL,
	git_url VARCHAR(500) NOT NULL,
	branch VARCHAR(160) NOT NULL,
	jenkins_job VARCHAR(255) NOT NULL,
	jenkins_base_url VARCHAR(500) NOT NULL,
	zentao_base_url VARCHAR(500) NOT NULL,
	product_id INTEGER NOT NULL,
	execution_id INTEGER,
	target_build_id INTEGER,
	default_testtask_id INTEGER,
	writeback_enabled BOOLEAN NOT NULL,
	integration_key_hash VARCHAR(64) NOT NULL,
	catalog_revision VARCHAR(64),
	catalog_json TEXT NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_automation_repositories_owner_id ON automation_repositories (owner_id)

-- statement-break

CREATE TABLE bug_tracking (
	id SERIAL NOT NULL,
	major_version_id INTEGER NOT NULL,
	requirement_id INTEGER,
	source_type bugsourcetype NOT NULL,
	source_ref VARCHAR(50),
	bug_id VARCHAR(30) NOT NULL,
	found_minor_version_id INTEGER,
	fixed_minor_version_id INTEGER,
	test_done BOOLEAN NOT NULL,
	newly_found_bug_id VARCHAR(30),
	resolution VARCHAR NOT NULL,
	closed BOOLEAN NOT NULL,
	created_by_id INTEGER,
	dispatched_to_id INTEGER,
	closed_by_id INTEGER,
	is_retest_failed BOOLEAN NOT NULL,
	retest_activated BOOLEAN NOT NULL,
	retest_activated_by_id INTEGER,
	retest_activated_at TIMESTAMP WITHOUT TIME ZONE,
	retest_activated_req_id INTEGER,
	retest_dismissed BOOLEAN NOT NULL,
	retest_dismissed_by_id INTEGER,
	zentao_bug_id VARCHAR(40),
	zentao_bug_url TEXT,
	zentao_client_record_id VARCHAR(120),
	zentao_source VARCHAR(40),
	zentao_captured_at BIGINT,
	zentao_top_href TEXT,
	zentao_product_id VARCHAR(40),
	zentao_product_name VARCHAR(255),
	zentao_project_id VARCHAR(40),
	zentao_project_name VARCHAR(255),
	zentao_opened_build_ids TEXT,
	zentao_affected_version VARCHAR(255),
	zentao_resolved_build TEXT,
	zentao_bug_title TEXT,
	zentao_source_type VARCHAR(30),
	zentao_linked_case_id VARCHAR(40),
	zentao_linked_case_label VARCHAR(120),
	zentao_linked_case_href TEXT,
	zentao_display_bucket VARCHAR(20),
	zentao_execution_id VARCHAR(80),
	zentao_execution_name VARCHAR(255),
	zentao_requirement_id VARCHAR(40),
	zentao_requirement_name TEXT,
	zentao_creator_name VARCHAR(100),
	zentao_sync_status VARCHAR(40),
	zentao_sync_message TEXT,
	zentao_sync_source VARCHAR(40),
	zentao_raw_payload TEXT,
	zentao_story_id INTEGER,
	zentao_build_id INTEGER,
	zentao_release_id INTEGER,
	zentao_testtask_id INTEGER,
	last_zentao_synced_at TIMESTAMP WITHOUT TIME ZONE,
	last_zentao_checked_at TIMESTAMP WITHOUT TIME ZONE,
	zentao_live_status VARCHAR(40),
	zentao_deleted BOOLEAN NOT NULL,
	zentao_closed_by_account VARCHAR(100),
	zentao_closed_by_name VARCHAR(100),
	zentao_close_date TIMESTAMP WITHOUT TIME ZONE,
	zentao_close_comment TEXT,
	zentao_assigned_to_account VARCHAR(100),
	zentao_assigned_to_name VARCHAR(100),
	zentao_remote_updated_at TIMESTAMP WITHOUT TIME ZONE,
	zentao_opened_at TIMESTAMP WITHOUT TIME ZONE,
	zentao_opened_by_account VARCHAR(100),
	zentao_opened_by_name VARCHAR(100),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_bug_tracking_zentao_bug_id UNIQUE (zentao_bug_id),
	CONSTRAINT uq_bug_tracking_zentao_client_record_id UNIQUE (zentao_client_record_id),
	FOREIGN KEY(major_version_id) REFERENCES versions (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(requirement_id) REFERENCES requirements (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(found_minor_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(fixed_minor_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(created_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(dispatched_to_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(closed_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(retest_activated_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(retest_dismissed_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_bug_tracking_zentao_linked_case_id ON bug_tracking (zentao_linked_case_id)

-- statement-break

CREATE INDEX ix_bug_tracking_zentao_bug_id ON bug_tracking (zentao_bug_id)

-- statement-break

CREATE INDEX ix_bug_tracking_retest_activated_req_id ON bug_tracking (retest_activated_req_id)

-- statement-break

CREATE INDEX ix_bug_tracking_requirement_source ON bug_tracking (requirement_id, source_type, source_ref)

-- statement-break

CREATE INDEX ix_bug_tracking_zentao_opened_at ON bug_tracking (zentao_opened_at)

-- statement-break

CREATE INDEX ix_bug_tracking_zentao_client_record_id ON bug_tracking (zentao_client_record_id)

-- statement-break

CREATE INDEX ix_bug_tracking_id ON bug_tracking (id)

-- statement-break

CREATE INDEX ix_bug_tracking_bug_id ON bug_tracking (bug_id)

-- statement-break

CREATE INDEX ix_bug_tracking_zentao_story_id ON bug_tracking (zentao_story_id)

-- statement-break

CREATE INDEX ix_bug_tracking_zentao_build_id ON bug_tracking (zentao_build_id)

-- statement-break

CREATE TABLE bugly_issues (
	id SERIAL NOT NULL,
	application_id INTEGER NOT NULL,
	source_issue_id VARCHAR(100),
	title VARCHAR(500) NOT NULL,
	status VARCHAR(30) NOT NULL,
	exception_type VARCHAR(100) NOT NULL,
	owner_id INTEGER,
	analysis TEXT NOT NULL,
	external_note TEXT NOT NULL,
	archived BOOLEAN NOT NULL,
	version INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (application_id, source_issue_id),
	FOREIGN KEY(application_id) REFERENCES bugly_applications (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(owner_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_bugly_issues_application_id ON bugly_issues (application_id)

-- statement-break

CREATE INDEX ix_bugly_issues_status ON bugly_issues (status)

-- statement-break

CREATE TABLE bugly_import_batches (
	id VARCHAR(36) NOT NULL,
	application_id INTEGER NOT NULL,
	actor_id INTEGER NOT NULL,
	filename VARCHAR(255) NOT NULL,
	file_hash VARCHAR(64) NOT NULL,
	status VARCHAR(20) NOT NULL,
	items JSON NOT NULL,
	diagnostics JSON NOT NULL,
	result JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	confirmed_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(application_id) REFERENCES bugly_applications (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(actor_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_bugly_import_batches_actor_id ON bugly_import_batches (actor_id)

-- statement-break

CREATE INDEX ix_bugly_import_batches_application_id ON bugly_import_batches (application_id)

-- statement-break

CREATE TABLE cad_versions (
	id SERIAL NOT NULL,
	board_id INTEGER NOT NULL,
	name VARCHAR(200) NOT NULL,
	sort_order INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(board_id) REFERENCES cad_boards (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_cad_versions_id ON cad_versions (id)

-- statement-break

CREATE INDEX ix_cad_versions_board_id ON cad_versions (board_id)

-- statement-break

CREATE TABLE cad_custom_columns (
	id SERIAL NOT NULL,
	board_id INTEGER NOT NULL,
	name VARCHAR(120) NOT NULL,
	sort_order INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(board_id) REFERENCES cad_boards (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_cad_custom_columns_board_id ON cad_custom_columns (board_id)

-- statement-break

CREATE INDEX ix_cad_custom_columns_id ON cad_custom_columns (id)

-- statement-break

CREATE TABLE cad_items (
	id SERIAL NOT NULL,
	board_id INTEGER NOT NULL,
	seq INTEGER,
	title VARCHAR(255),
	zentao_bug_id INTEGER,
	sort_order INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(board_id) REFERENCES cad_boards (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_cad_items_board_id ON cad_items (board_id)

-- statement-break

CREATE INDEX ix_cad_items_zentao_bug_id ON cad_items (zentao_bug_id)

-- statement-break

CREATE INDEX ix_cad_items_id ON cad_items (id)

-- statement-break

CREATE TABLE test_case_reviews (
	id SERIAL NOT NULL,
	case_key VARCHAR(60) NOT NULL,
	requirement_id INTEGER,
	reviewer_id INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	opinion TEXT,
	active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(requirement_id) REFERENCES requirements (id) ON DELETE SET NULL DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(reviewer_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_test_case_reviews_requirement_id ON test_case_reviews (requirement_id)

-- statement-break

CREATE INDEX ix_test_case_reviews_id ON test_case_reviews (id)

-- statement-break

CREATE INDEX ix_test_case_reviews_case_key ON test_case_reviews (case_key)

-- statement-break

CREATE INDEX ix_test_case_reviews_case_active ON test_case_reviews (case_key, active)

-- statement-break

CREATE INDEX ix_test_case_reviews_reviewer_id ON test_case_reviews (reviewer_id)

-- statement-break

CREATE TABLE test_executions (
	id SERIAL NOT NULL,
	requirement_id INTEGER NOT NULL,
	minor_version_id INTEGER NOT NULL,
	bug_id VARCHAR(30),
	source_case_id VARCHAR(30),
	result_status testresultstatus NOT NULL,
	notes TEXT,
	executed_by_id INTEGER,
	executed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(requirement_id) REFERENCES requirements (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(minor_version_id) REFERENCES versions (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(executed_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_test_executions_bug_id ON test_executions (bug_id)

-- statement-break

CREATE INDEX ix_test_executions_id ON test_executions (id)

-- statement-break

CREATE TABLE feedback_attachments (
	id SERIAL NOT NULL,
	feedback_id INTEGER NOT NULL,
	original_name VARCHAR(255) NOT NULL,
	stored_name VARCHAR(255) NOT NULL,
	file_path VARCHAR(500) NOT NULL,
	file_type VARCHAR(120),
	file_ext VARCHAR(20),
	file_size INTEGER NOT NULL,
	is_image BOOLEAN NOT NULL,
	uploaded_by_id INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(feedback_id) REFERENCES feedback_records (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(uploaded_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_feedback_attachments_stored_name ON feedback_attachments (stored_name)

-- statement-break

CREATE INDEX ix_feedback_attachments_id ON feedback_attachments (id)

-- statement-break

CREATE INDEX ix_feedback_attachments_feedback_id ON feedback_attachments (feedback_id)

-- statement-break

CREATE TABLE field_test_records (
	id SERIAL NOT NULL,
	major_version_id INTEGER NOT NULL,
	minor_version_id INTEGER NOT NULL,
	purpose_type fieldtestpurposetype NOT NULL,
	requirement_id INTEGER,
	test_content VARCHAR(255),
	start_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	end_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	duration_minutes INTEGER NOT NULL,
	result_status fieldtestresultstatus NOT NULL,
	tester_id INTEGER NOT NULL,
	notes TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(major_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(minor_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(requirement_id) REFERENCES requirements (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(tester_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_field_test_records_purpose_type ON field_test_records (purpose_type)

-- statement-break

CREATE INDEX ix_field_test_records_requirement_id ON field_test_records (requirement_id)

-- statement-break

CREATE INDEX ix_field_test_records_result_status ON field_test_records (result_status)

-- statement-break

CREATE INDEX ix_field_test_records_id ON field_test_records (id)

-- statement-break

CREATE INDEX ix_field_test_records_minor_version_id ON field_test_records (minor_version_id)

-- statement-break

CREATE INDEX ix_field_test_records_created_at ON field_test_records (created_at)

-- statement-break

CREATE INDEX ix_field_test_records_tester_id ON field_test_records (tester_id)

-- statement-break

CREATE INDEX ix_field_test_records_major_version_id ON field_test_records (major_version_id)

-- statement-break

CREATE TABLE final_test_records (
	id SERIAL NOT NULL,
	requirement_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	case_completed BOOLEAN NOT NULL,
	test_completed BOOLEAN NOT NULL,
	testing BOOLEAN DEFAULT '0' NOT NULL,
	test_completed_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_final_test_records_req_user UNIQUE (requirement_id, user_id),
	FOREIGN KEY(requirement_id) REFERENCES requirements (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(user_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_final_test_records_id ON final_test_records (id)

-- statement-break

CREATE INDEX ix_final_test_records_user_id ON final_test_records (user_id)

-- statement-break

CREATE INDEX ix_final_test_records_requirement_id ON final_test_records (requirement_id)

-- statement-break

CREATE INDEX ix_final_test_records_test_completed_at ON final_test_records (test_completed_at)

-- statement-break

CREATE TABLE learning_materials (
	id SERIAL NOT NULL,
	topic_id INTEGER NOT NULL,
	original_name VARCHAR(255) NOT NULL,
	stored_name VARCHAR(255) NOT NULL,
	file_path VARCHAR(500) NOT NULL,
	file_type VARCHAR(120),
	file_ext VARCHAR(20),
	file_size INTEGER NOT NULL,
	is_image BOOLEAN NOT NULL,
	preview_pdf_path VARCHAR(500),
	uploaded_by_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(topic_id) REFERENCES learning_topics (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(uploaded_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_learning_materials_topic_id ON learning_materials (topic_id)

-- statement-break

CREATE INDEX ix_learning_materials_id ON learning_materials (id)

-- statement-break

CREATE INDEX ix_learning_materials_stored_name ON learning_materials (stored_name)

-- statement-break

CREATE TABLE assessments (
	id SERIAL NOT NULL,
	topic_id INTEGER NOT NULL,
	round_no INTEGER NOT NULL,
	author_id INTEGER,
	status assessmentstatus NOT NULL,
	total_score INTEGER NOT NULL,
	due_at TIMESTAMP WITHOUT TIME ZONE,
	published_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(topic_id) REFERENCES learning_topics (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(author_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_assessments_topic_id ON assessments (topic_id)

-- statement-break

CREATE INDEX ix_assessments_status ON assessments (status)

-- statement-break

CREATE INDEX ix_assessments_id ON assessments (id)

-- statement-break

CREATE INDEX ix_assessments_created_at ON assessments (created_at)

-- statement-break

CREATE INDEX ix_assessments_author_id ON assessments (author_id)

-- statement-break

CREATE TABLE learning_content_attachments (
	id SERIAL NOT NULL,
	content_id INTEGER NOT NULL,
	original_name VARCHAR(255) NOT NULL,
	stored_name VARCHAR(255) NOT NULL,
	file_path VARCHAR(500) NOT NULL,
	file_type VARCHAR(120),
	file_ext VARCHAR(20),
	file_size INTEGER NOT NULL,
	preview_pdf_path VARCHAR(500),
	uploaded_by_id INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(content_id) REFERENCES learning_contents (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(uploaded_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_learning_content_attachments_content_id ON learning_content_attachments (content_id)

-- statement-break

CREATE INDEX ix_learning_content_attachments_id ON learning_content_attachments (id)

-- statement-break

CREATE UNIQUE INDEX ix_learning_content_attachments_stored_name ON learning_content_attachments (stored_name)

-- statement-break

CREATE TABLE learning_questions (
	id SERIAL NOT NULL,
	quiz_id INTEGER NOT NULL,
	position INTEGER NOT NULL,
	question_type learning_question_type NOT NULL,
	prompt TEXT NOT NULL,
	score FLOAT NOT NULL,
	options_json TEXT,
	correct_answers_json TEXT,
	fill_grading_mode learning_fill_grading_mode,
	match_count INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(quiz_id) REFERENCES learning_quizzes (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_learning_questions_id ON learning_questions (id)

-- statement-break

CREATE INDEX ix_learning_questions_quiz_id ON learning_questions (quiz_id)

-- statement-break

CREATE TABLE learning_submissions (
	id SERIAL NOT NULL,
	quiz_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	is_preview BOOLEAN NOT NULL,
	status VARCHAR(20) NOT NULL,
	auto_score FLOAT NOT NULL,
	total_score FLOAT,
	submitted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	graded_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_quiz_user_preview UNIQUE (quiz_id, user_id, is_preview),
	FOREIGN KEY(quiz_id) REFERENCES learning_quizzes (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(user_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_learning_submissions_quiz_id ON learning_submissions (quiz_id)

-- statement-break

CREATE INDEX ix_learning_submissions_id ON learning_submissions (id)

-- statement-break

CREATE INDEX ix_learning_submissions_user_id ON learning_submissions (user_id)

-- statement-break

CREATE TABLE requirement_retest_records (
	id SERIAL NOT NULL,
	requirement_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	passed BOOLEAN NOT NULL,
	minor_version_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_req_retest_user UNIQUE (requirement_id, user_id),
	FOREIGN KEY(requirement_id) REFERENCES requirements (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(minor_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_requirement_retest_records_requirement_id ON requirement_retest_records (requirement_id)

-- statement-break

CREATE INDEX ix_requirement_retest_records_user_id ON requirement_retest_records (user_id)

-- statement-break

CREATE INDEX ix_requirement_retest_records_id ON requirement_retest_records (id)

-- statement-break

CREATE TABLE requirement_status_history (
	id SERIAL NOT NULL,
	requirement_id INTEGER NOT NULL,
	from_status requirementstatus,
	to_status requirementstatus NOT NULL,
	changed_by_id INTEGER,
	changed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(requirement_id) REFERENCES requirements (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(changed_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_requirement_status_history_to_status ON requirement_status_history (to_status)

-- statement-break

CREATE INDEX ix_requirement_status_history_id ON requirement_status_history (id)

-- statement-break

CREATE INDEX ix_requirement_status_history_changed_at ON requirement_status_history (changed_at)

-- statement-break

CREATE INDEX ix_requirement_status_history_requirement_id ON requirement_status_history (requirement_id)

-- statement-break

CREATE INDEX ix_requirement_status_history_changed_by_id ON requirement_status_history (changed_by_id)

-- statement-break

CREATE TABLE zentao_ai_case_drafts (
	id VARCHAR(36) NOT NULL,
	workspace_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	content_json TEXT NOT NULL,
	sources_json TEXT NOT NULL,
	parents_json TEXT NOT NULL,
	active BOOLEAN NOT NULL,
	revision INTEGER NOT NULL,
	submit_status VARCHAR(20) NOT NULL,
	push_item_id VARCHAR(36),
	remote_case_id INTEGER,
	error TEXT,
	warning TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(workspace_id) REFERENCES zentao_ai_case_workspaces (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_zentao_ai_case_drafts_submit_status ON zentao_ai_case_drafts (submit_status)

-- statement-break

CREATE INDEX ix_zentao_ai_case_drafts_workspace_id ON zentao_ai_case_drafts (workspace_id)

-- statement-break

CREATE TABLE zentao_ai_case_push_batches (
	id VARCHAR(36) NOT NULL,
	workspace_id VARCHAR(36) NOT NULL,
	user_id INTEGER NOT NULL,
	request_id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (user_id, request_id),
	FOREIGN KEY(workspace_id) REFERENCES zentao_ai_case_workspaces (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(user_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_zentao_ai_case_push_batches_user_id ON zentao_ai_case_push_batches (user_id)

-- statement-break

CREATE INDEX ix_zentao_ai_case_push_batches_workspace_id ON zentao_ai_case_push_batches (workspace_id)

-- statement-break

CREATE TABLE task_board_updates (
	id SERIAL NOT NULL,
	task_id INTEGER NOT NULL,
	author_id INTEGER,
	content TEXT NOT NULL,
	status_snapshot VARCHAR(40),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES task_board_tasks (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(author_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_task_board_updates_id ON task_board_updates (id)

-- statement-break

CREATE INDEX ix_task_board_updates_task_id ON task_board_updates (task_id)

-- statement-break

CREATE INDEX ix_task_board_updates_created_at ON task_board_updates (created_at)

-- statement-break

CREATE TABLE test_cases (
	id SERIAL NOT NULL,
	requirement_id INTEGER NOT NULL,
	zentao_case_id VARCHAR(30) NOT NULL,
	zentao_case_url TEXT,
	zentao_client_record_id VARCHAR(120),
	zentao_source VARCHAR(40),
	zentao_captured_at BIGINT,
	zentao_top_href TEXT,
	zentao_product_id VARCHAR(40),
	zentao_product_name VARCHAR(255),
	zentao_case_title TEXT,
	zentao_requirement_id VARCHAR(40),
	zentao_requirement_name TEXT,
	zentao_creator_name VARCHAR(100),
	zentao_sync_status VARCHAR(40),
	zentao_sync_message TEXT,
	zentao_raw_payload TEXT,
	zentao_case_numeric_id INTEGER,
	creator_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_test_cases_zentao_client_record_id UNIQUE (zentao_client_record_id),
	FOREIGN KEY(requirement_id) REFERENCES requirements (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(creator_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_test_cases_zentao_case_id ON test_cases (zentao_case_id)

-- statement-break

CREATE INDEX ix_test_cases_id ON test_cases (id)

-- statement-break

CREATE INDEX ix_test_cases_zentao_case_numeric_id ON test_cases (zentao_case_numeric_id)

-- statement-break

CREATE INDEX ix_test_cases_zentao_client_record_id ON test_cases (zentao_client_record_id)

-- statement-break

CREATE TABLE automation_runs (
	id VARCHAR(36) NOT NULL,
	repository_id INTEGER NOT NULL,
	requested_by_id INTEGER NOT NULL,
	idempotency_key VARCHAR(100) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	source VARCHAR(20) NOT NULL,
	external_actor VARCHAR(120),
	dry_run BOOLEAN NOT NULL,
	status VARCHAR(30) NOT NULL,
	revision VARCHAR(64) NOT NULL,
	branch VARCHAR(160) NOT NULL,
	testtask_id INTEGER NOT NULL,
	device_serial VARCHAR(160) NOT NULL,
	apk_reference VARCHAR(500) NOT NULL,
	plan_json TEXT NOT NULL,
	parameters_json TEXT NOT NULL,
	queue_url VARCHAR(700),
	build_number INTEGER,
	build_url VARCHAR(700),
	result_hash VARCHAR(64),
	summary_json TEXT NOT NULL,
	message VARCHAR(500),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_automation_run_request UNIQUE (repository_id, idempotency_key),
	FOREIGN KEY(repository_id) REFERENCES automation_repositories (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(requested_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_automation_runs_created_at ON automation_runs (created_at)

-- statement-break

CREATE INDEX ix_automation_runs_repository_id ON automation_runs (repository_id)

-- statement-break

CREATE INDEX ix_automation_runs_status ON automation_runs (status)

-- statement-break

CREATE TABLE browser_sync_events (
	id SERIAL NOT NULL,
	client_record_id VARCHAR(120) NOT NULL,
	entity_type VARCHAR(20) NOT NULL,
	action VARCHAR(30),
	source VARCHAR(40),
	captured_at BIGINT,
	received_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	top_href TEXT,
	page_url TEXT,
	page_type VARCHAR(60),
	script_version VARCHAR(60),
	draft_json TEXT,
	result_json TEXT,
	raw_payload_json TEXT,
	push_message TEXT,
	creator_name VARCHAR(100),
	operator_name VARCHAR(100),
	zentao_bug_id VARCHAR(40),
	zentao_case_id VARCHAR(40),
	zentao_req_id VARCHAR(40),
	zentao_requirement_name TEXT,
	zentao_product_id VARCHAR(40),
	zentao_product_name VARCHAR(255),
	zentao_project_id VARCHAR(40),
	zentao_project_name VARCHAR(255),
	zentao_execution_name VARCHAR(255),
	zentao_affected_version VARCHAR(255),
	zentao_case_title TEXT,
	zentao_bug_title TEXT,
	source_type VARCHAR(30),
	linked_case_id VARCHAR(40),
	linked_case_label VARCHAR(120),
	linked_case_href TEXT,
	display_bucket VARCHAR(20) NOT NULL,
	status VARCHAR(30) NOT NULL,
	failure_reason TEXT,
	mapped_requirement_id INTEGER,
	mapped_major_version_id INTEGER,
	mapped_minor_version_id INTEGER,
	mapped_source_type VARCHAR(30),
	mapped_source_ref VARCHAR(80),
	mapped_test_case_id INTEGER,
	applied_case_id INTEGER,
	applied_bug_tracking_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(mapped_requirement_id) REFERENCES requirements (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(mapped_major_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(mapped_minor_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(mapped_test_case_id) REFERENCES test_cases (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(applied_case_id) REFERENCES test_cases (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(applied_bug_tracking_id) REFERENCES bug_tracking (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_browser_sync_events_id ON browser_sync_events (id)

-- statement-break

CREATE INDEX ix_browser_sync_events_status ON browser_sync_events (status)

-- statement-break

CREATE INDEX ix_browser_sync_events_entity_type ON browser_sync_events (entity_type)

-- statement-break

CREATE INDEX ix_browser_sync_events_zentao_req_id ON browser_sync_events (zentao_req_id)

-- statement-break

CREATE INDEX ix_browser_sync_events_zentao_bug_id ON browser_sync_events (zentao_bug_id)

-- statement-break

CREATE UNIQUE INDEX ix_browser_sync_events_client_record_id ON browser_sync_events (client_record_id)

-- statement-break

CREATE INDEX ix_browser_sync_events_display_bucket ON browser_sync_events (display_bucket)

-- statement-break

CREATE INDEX ix_browser_sync_events_zentao_case_id ON browser_sync_events (zentao_case_id)

-- statement-break

CREATE TABLE bugly_reports (
	id SERIAL NOT NULL,
	issue_id INTEGER NOT NULL,
	source_report_id VARCHAR(100),
	app_version VARCHAR(255) NOT NULL,
	device VARCHAR(255) NOT NULL,
	os_version VARCHAR(255) NOT NULL,
	occurred_at TIMESTAMP WITHOUT TIME ZONE,
	occurred_at_raw VARCHAR(255) NOT NULL,
	collected_at TIMESTAMP WITHOUT TIME ZONE,
	stack_text TEXT NOT NULL,
	stack_mode VARCHAR(30) NOT NULL,
	frame_count INTEGER NOT NULL,
	payload JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	revision INTEGER NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (issue_id, source_report_id),
	FOREIGN KEY(issue_id) REFERENCES bugly_issues (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_bugly_reports_issue_id ON bugly_reports (issue_id)

-- statement-break

CREATE INDEX ix_bugly_reports_occurred_at ON bugly_reports (occurred_at)

-- statement-break

CREATE INDEX ix_bugly_reports_app_version ON bugly_reports (app_version)

-- statement-break

CREATE INDEX ix_bugly_reports_device ON bugly_reports (device)

-- statement-break

CREATE TABLE bugly_grants (
	id SERIAL NOT NULL,
	user_id INTEGER NOT NULL,
	application_id INTEGER NOT NULL,
	issue_id INTEGER,
	scope VARCHAR(100) NOT NULL,
	role VARCHAR(20) NOT NULL,
	can_sensitive BOOLEAN NOT NULL,
	can_publish BOOLEAN NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	granted_by INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (user_id, application_id, scope),
	FOREIGN KEY(user_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(application_id) REFERENCES bugly_applications (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(issue_id) REFERENCES bugly_issues (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(granted_by) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_bugly_grants_application_id ON bugly_grants (application_id)

-- statement-break

CREATE INDEX ix_bugly_grants_user_id ON bugly_grants (user_id)

-- statement-break

CREATE INDEX ix_bugly_grants_issue_id ON bugly_grants (issue_id)

-- statement-break

CREATE TABLE bugly_publications (
	issue_id INTEGER NOT NULL,
	snapshot JSON NOT NULL,
	source_version INTEGER NOT NULL,
	version INTEGER NOT NULL,
	active BOOLEAN NOT NULL,
	published_by INTEGER NOT NULL,
	published_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (issue_id),
	FOREIGN KEY(issue_id) REFERENCES bugly_issues (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(published_by) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE TABLE cad_records (
	id SERIAL NOT NULL,
	item_id INTEGER NOT NULL,
	version_id INTEGER NOT NULL,
	normal_count INTEGER NOT NULL,
	abnormal_count INTEGER NOT NULL,
	description TEXT,
	custom_values TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_cad_record_item_version UNIQUE (item_id, version_id),
	FOREIGN KEY(item_id) REFERENCES cad_items (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(version_id) REFERENCES cad_versions (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_cad_records_version_id ON cad_records (version_id)

-- statement-break

CREATE INDEX ix_cad_records_item_id ON cad_records (item_id)

-- statement-break

CREATE INDEX ix_cad_records_id ON cad_records (id)

-- statement-break

CREATE TABLE cad_item_folders (
	id SERIAL NOT NULL,
	item_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	uploaded_by_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(item_id) REFERENCES cad_items (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(uploaded_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_cad_item_folders_item_id ON cad_item_folders (item_id)

-- statement-break

CREATE INDEX ix_cad_item_folders_id ON cad_item_folders (id)

-- statement-break

CREATE TABLE feedback_bug_links (
	id SERIAL NOT NULL,
	feedback_id INTEGER NOT NULL,
	bug_id INTEGER NOT NULL,
	created_by_id INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_feedback_bug_link UNIQUE (feedback_id, bug_id),
	FOREIGN KEY(feedback_id) REFERENCES feedback_records (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(bug_id) REFERENCES bug_tracking (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(created_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_feedback_bug_links_id ON feedback_bug_links (id)

-- statement-break

CREATE INDEX ix_feedback_bug_links_feedback_id ON feedback_bug_links (feedback_id)

-- statement-break

CREATE INDEX ix_feedback_bug_links_bug_id ON feedback_bug_links (bug_id)

-- statement-break

CREATE TABLE field_test_bug_links (
	id SERIAL NOT NULL,
	field_test_record_id INTEGER NOT NULL,
	bug_tracking_id INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_field_test_bug_link UNIQUE (field_test_record_id, bug_tracking_id),
	FOREIGN KEY(field_test_record_id) REFERENCES field_test_records (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(bug_tracking_id) REFERENCES bug_tracking (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_field_test_bug_links_field_test_record_id ON field_test_bug_links (field_test_record_id)

-- statement-break

CREATE INDEX ix_field_test_bug_links_id ON field_test_bug_links (id)

-- statement-break

CREATE INDEX ix_field_test_bug_links_bug_tracking_id ON field_test_bug_links (bug_tracking_id)

-- statement-break

CREATE TABLE assessment_questions (
	id SERIAL NOT NULL,
	assessment_id INTEGER NOT NULL,
	type questiontype NOT NULL,
	prompt TEXT NOT NULL,
	options_json TEXT,
	answer_json TEXT,
	score INTEGER NOT NULL,
	sort_order INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(assessment_id) REFERENCES assessments (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_assessment_questions_id ON assessment_questions (id)

-- statement-break

CREATE INDEX ix_assessment_questions_assessment_id ON assessment_questions (assessment_id)

-- statement-break

CREATE TABLE assessment_submissions (
	id SERIAL NOT NULL,
	assessment_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	status submissionstatus NOT NULL,
	objective_score INTEGER NOT NULL,
	subjective_score INTEGER NOT NULL,
	total_score INTEGER NOT NULL,
	submitted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	graded_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_assessment_submission_user UNIQUE (assessment_id, user_id),
	FOREIGN KEY(assessment_id) REFERENCES assessments (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(user_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_assessment_submissions_status ON assessment_submissions (status)

-- statement-break

CREATE INDEX ix_assessment_submissions_assessment_id ON assessment_submissions (assessment_id)

-- statement-break

CREATE INDEX ix_assessment_submissions_id ON assessment_submissions (id)

-- statement-break

CREATE INDEX ix_assessment_submissions_user_id ON assessment_submissions (user_id)

-- statement-break

CREATE TABLE learning_answers (
	id SERIAL NOT NULL,
	submission_id INTEGER NOT NULL,
	question_id INTEGER NOT NULL,
	answer_json TEXT NOT NULL,
	is_correct BOOLEAN,
	awarded_score FLOAT,
	reviewer_comment TEXT,
	PRIMARY KEY (id),
	CONSTRAINT uq_submission_question UNIQUE (submission_id, question_id),
	FOREIGN KEY(submission_id) REFERENCES learning_submissions (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(question_id) REFERENCES learning_questions (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_learning_answers_id ON learning_answers (id)

-- statement-break

CREATE INDEX ix_learning_answers_question_id ON learning_answers (question_id)

-- statement-break

CREATE INDEX ix_learning_answers_submission_id ON learning_answers (submission_id)

-- statement-break

CREATE TABLE bug_stage5_records (
	id SERIAL NOT NULL,
	bug_tracking_id INTEGER,
	user_id INTEGER,
	minor_version_id INTEGER,
	test_done BOOLEAN,
	newly_found_bug_id VARCHAR,
	resolution VARCHAR,
	source VARCHAR(20) DEFAULT 'manual' NOT NULL,
	comment VARCHAR,
	updated_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(bug_tracking_id) REFERENCES bug_tracking (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(user_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(minor_version_id) REFERENCES versions (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_bug_stage5_records_id ON bug_stage5_records (id)

-- statement-break

CREATE TABLE zentao_ai_case_push_items (
	id VARCHAR(36) NOT NULL,
	batch_id VARCHAR(36) NOT NULL,
	draft_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	snapshot_json TEXT NOT NULL,
	status VARCHAR(20) NOT NULL,
	remote_case_id INTEGER,
	error TEXT,
	warning TEXT,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (batch_id, draft_id),
	FOREIGN KEY(batch_id) REFERENCES zentao_ai_case_push_batches (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(draft_id) REFERENCES zentao_ai_case_drafts (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_zentao_ai_case_push_items_status ON zentao_ai_case_push_items (status)

-- statement-break

CREATE INDEX ix_zentao_ai_case_push_items_draft_id ON zentao_ai_case_push_items (draft_id)

-- statement-break

CREATE INDEX ix_zentao_ai_case_push_items_batch_id ON zentao_ai_case_push_items (batch_id)

-- statement-break

CREATE TABLE automation_case_results (
	id SERIAL NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	case_id INTEGER NOT NULL,
	case_version INTEGER NOT NULL,
	test_name VARCHAR(500) NOT NULL,
	status VARCHAR(20) NOT NULL,
	duration_seconds VARCHAR(40) NOT NULL,
	message TEXT NOT NULL,
	evidence_json TEXT NOT NULL,
	sync_status VARCHAR(20) NOT NULL,
	sync_message VARCHAR(500),
	zentao_result_id INTEGER,
	synced_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_automation_result_case UNIQUE (run_id, case_id),
	FOREIGN KEY(run_id) REFERENCES automation_runs (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_automation_case_results_run_id ON automation_case_results (run_id)

-- statement-break

CREATE INDEX ix_automation_case_results_sync_status ON automation_case_results (sync_status)

-- statement-break

CREATE TABLE bugly_report_revisions (
	id SERIAL NOT NULL,
	report_id INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	payload JSON NOT NULL,
	actor_id INTEGER NOT NULL,
	batch_id VARCHAR(36),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(report_id) REFERENCES bugly_reports (id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(actor_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_bugly_report_revisions_batch_id ON bugly_report_revisions (batch_id)

-- statement-break

CREATE INDEX ix_bugly_report_revisions_report_id ON bugly_report_revisions (report_id)

-- statement-break

CREATE TABLE cad_attachments (
	id SERIAL NOT NULL,
	record_id INTEGER NOT NULL,
	kind VARCHAR(20) NOT NULL,
	original_name VARCHAR(255) NOT NULL,
	stored_name VARCHAR(120) NOT NULL,
	file_path TEXT NOT NULL,
	file_type VARCHAR(120),
	file_ext VARCHAR(20),
	file_size INTEGER NOT NULL,
	is_image BOOLEAN NOT NULL,
	uploaded_by_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(record_id) REFERENCES cad_records (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(uploaded_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_cad_attachments_record_id ON cad_attachments (record_id)

-- statement-break

CREATE INDEX ix_cad_attachments_id ON cad_attachments (id)

-- statement-break

CREATE TABLE cad_item_files (
	id SERIAL NOT NULL,
	item_id INTEGER NOT NULL,
	folder_id INTEGER,
	rel_path TEXT,
	original_name VARCHAR(255) NOT NULL,
	stored_name VARCHAR(120) NOT NULL,
	file_path TEXT NOT NULL,
	file_type VARCHAR(120),
	file_ext VARCHAR(20),
	file_size INTEGER NOT NULL,
	uploaded_by_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(item_id) REFERENCES cad_items (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(folder_id) REFERENCES cad_item_folders (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(uploaded_by_id) REFERENCES users (id) DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_cad_item_files_item_id ON cad_item_files (item_id)

-- statement-break

CREATE INDEX ix_cad_item_files_id ON cad_item_files (id)

-- statement-break

CREATE INDEX ix_cad_item_files_folder_id ON cad_item_files (folder_id)

-- statement-break

CREATE TABLE assessment_answers (
	id SERIAL NOT NULL,
	submission_id INTEGER NOT NULL,
	question_id INTEGER NOT NULL,
	response_json TEXT,
	auto_graded BOOLEAN NOT NULL,
	score INTEGER NOT NULL,
	grader_comment TEXT,
	PRIMARY KEY (id),
	FOREIGN KEY(submission_id) REFERENCES assessment_submissions (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(question_id) REFERENCES assessment_questions (id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
)

-- statement-break

CREATE INDEX ix_assessment_answers_question_id ON assessment_answers (question_id)

-- statement-break

CREATE INDEX ix_assessment_answers_id ON assessment_answers (id)

-- statement-break

CREATE INDEX ix_assessment_answers_submission_id ON assessment_answers (submission_id)
