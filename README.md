# Production Scheduling Dashboard

## Description

This is a Flask-based web application that provides a dashboard for visualizing and managing production schedules. It uses a sophisticated scheduling engine powered by Google's CP-SAT solver to optimize task assignments based on various constraints, such as resource availability, task dependencies, and delivery dates.

The application allows users to:
-   View production schedules for different scenarios.
-   Analyze resource utilization and team capacities.
-   Automatically assign tasks to mechanics, quality inspectors, and customer representatives.
-   Validate the feasibility of generated schedules.

## Features

-   **Scenario-based Scheduling**: Run and compare different scheduling scenarios (e.g., baseline, optimized).
-   **Smart Auto-Assignment**: Automatically assign tasks to available resources while respecting constraints and avoiding conflicts.
-   **Interactive Dashboard**: A web-based UI to visualize schedules, resource utilization, and product delivery timelines.
-   **CP-SAT Optimization**: Leverages Google's CP-SAT solver to find optimal schedules that minimize lateness.
-   **Data-Driven**: Schedule generation is based on data provided in a `scheduling_data.csv` file.
-   **Comprehensive Validation**: Includes a standalone script to validate schedule feasibility and identify constraint violations.

## Getting Started

### Prerequisites

-   Python 3.12 or higher
-   pip package manager

### Installation

1.  Clone the repository:
    ```bash
    git clone <repository_url>
    cd <repository_directory>
    ```

2.  Install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```

### Running the Application

To run the web application, execute the `run.py` script:
```bash
python run.py
```
The application will be available at `http://127.0.0.1:5000`.

## Usage

### Dashboard

The main dashboard can be accessed by opening `http://127.0.0.1:5000` in a web browser. The dashboard provides an interactive view of the production schedule, resource utilization, and other metrics.

### API Endpoints

The application exposes several API endpoints for programmatic access to the scheduling data. Here are some of the key endpoints:

-   `GET /api/scenarios/<scenario_id>`: Get the schedule data for a specific scenario.
-   `POST /api/auto_assign`: Trigger the smart auto-assignment feature.
-   `GET /api/team/<team_name>/tasks`: Get the tasks assigned to a specific team.
-   `GET /api/mechanic/<mechanic_id>/assigned_tasks`: Get the tasks assigned to a specific mechanic.

## Data Format (`scheduling_data.csv`)

The application uses a CSV file named `scheduling_data.csv` to load all the necessary data for scheduling. The file is divided into several sections, each marked with `==== SECTION NAME ====`.

The key sections include:
-   **SHIFT WORKING HOURS**: Defines the working hours for different shifts.
-   **MECHANIC TEAM CAPACITY**: Specifies the number of mechanics available in each team.
-   **QUALITY TEAM CAPACITY**: Specifies the number of quality inspectors available in each team.
-   **CUSTOMER TEAM CAPACITY**: Specifies the number of customer representatives available in each team.
-   **TASK RELATIONSHIPS TABLE**: Defines the precedence constraints between tasks.
-   **TASK DURATION AND RESOURCE TABLE**: Specifies the duration and resource requirements for each task.
-   **PRODUCT LINE DELIVERY SCHEDULE**: Defines the delivery dates for each product line.
-   ... and more.

## Validation

The project includes a validation script to check the feasibility of the generated schedules. To run the validation script, use the following command:
```bash
python scheduler_validation_script.py
```
The script can also be run for specific scenarios:
```bash
python scheduler_validation_script.py --scenario 1
```

## Technology Stack

-   **Backend**: Flask, Python
-   **Scheduling Engine**: Google OR-Tools (CP-SAT)
-   **Data Processing**: Pandas, NumPy
-   **Frontend**: HTML, CSS, JavaScript (The frontend code is not fully explored in this context, but it's part of the application)
