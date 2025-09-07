// dashboard.js - Enhanced Client-side JavaScript for Production Scheduling Dashboard
// Compatible with product-specific late parts and rework tasks

let currentScenario = 'baseline';
let currentView = 'team-lead';
let selectedTeam = 'all';
let selectedShift = 'all';
let selectedProduct = 'all';
let scenarioData = {};
let allScenarios = {};
let mechanicAvailability = {};
let taskAssignments = {};
let savedAssignments = {}; // Store assignments per scenario

// Initialize dashboard on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('Initializing Production Scheduling Dashboard...');
    loadAllScenarios();
    setupEventListeners();
    setupProductFilter();
    setupRefreshButton();
});

// Load all scenarios at startup for quick switching
// Load all scenarios at startup for quick switching
async function loadAllScenarios() {
    try {
        showLoading('Loading scenario data...');
        const scenariosResponse = await fetch('/api/scenarios');
        const scenariosInfo = await scenariosResponse.json();

        console.log('Loading scenarios:', scenariosInfo.scenarios.map(s => s.id));

        // Load each scenario
        for (const scenario of scenariosInfo.scenarios) {
            const response = await fetch(`/api/scenario/${scenario.id}`);
            if (response.ok) {
                const data = await response.json();
                allScenarios[scenario.id] = data;
                console.log(`✓ Loaded ${scenario.id}: ${data.tasks ? data.tasks.length : 0} tasks`);
            } else {
                console.error(`✗ Failed to load ${scenario.id}`);
            }
        }

        // Set the initial scenario data - MAKE SURE THIS IS CORRECT
        if (allScenarios[currentScenario] && allScenarios[currentScenario].tasks) {
            scenarioData = allScenarios[currentScenario];
            console.log('Set scenarioData to', currentScenario, 'with', scenarioData.tasks.length, 'tasks');
        } else if (allScenarios['baseline'] && allScenarios['baseline'].tasks) {
            currentScenario = 'baseline';
            scenarioData = allScenarios['baseline'];
            console.log('Fallback to baseline with', scenarioData.tasks.length, 'tasks');
        } else {
            console.error('No valid scenarios loaded!');
            console.log('allScenarios:', allScenarios);
        }

        hideLoading();

        // Verify the data structure
        console.log('Final scenarioData keys:', Object.keys(scenarioData));
        console.log('Has tasks?', !!scenarioData.tasks);
        console.log('Task count:', scenarioData.tasks?.length || 0);

        if (scenarioData && scenarioData.tasks && scenarioData.tasks.length > 0) {
            populateTeamDropdowns();  // ADD THIS LINE
            updateView();
        } else {
            console.error('ScenarioData is missing tasks!');
            showError('No task data available. Please check the server.');
        }
    } catch (error) {
        console.error('Error loading scenarios:', error);
        hideLoading();
        showError('Failed to load scenario data. Please refresh the page.');
    }
}

// Setup all event listeners
function setupEventListeners() {
    // View tab switching
    document.querySelectorAll('.view-tab').forEach(tab => {
        tab.addEventListener('click', function() {
            switchView(this.dataset.view);
        });
    });

    // Scenario selection
    const scenarioSelect = document.getElementById('scenarioSelect');
    if (scenarioSelect) {
        scenarioSelect.addEventListener('change', function() {
            switchScenario(this.value);
        });
    }

    // Team selection - UPDATED to include shift dropdown update
    const teamSelect = document.getElementById('teamSelect');
    if (teamSelect) {
        teamSelect.addEventListener('change', function() {
            selectedTeam = this.value;
            updateShiftDropdown();  // <-- Update shifts based on team selection
            updateTeamLeadView();
        });
    }

    // Shift selection
    const shiftSelect = document.getElementById('shiftSelect');
    if (shiftSelect) {
        shiftSelect.addEventListener('change', function() {
            selectedShift = this.value;
            updateTeamLeadView();
        });
    }

    // Product selection (if product filter exists)
    const productSelect = document.getElementById('productSelect');
    if (productSelect) {
        productSelect.addEventListener('change', function() {
            selectedProduct = this.value;
            updateTeamLeadView();
        });
    }

    // Mechanic selection for individual view
    const mechanicSelect = document.getElementById('mechanicSelect');
    if (mechanicSelect) {
        mechanicSelect.addEventListener('change', function() {
            updateMechanicView();
        });
    }

    // Auto-assign button
    const autoAssignBtn = document.querySelector('button[onclick="autoAssign()"]');
    if (autoAssignBtn && !autoAssignBtn.hasAttribute('data-listener-added')) {
        autoAssignBtn.setAttribute('data-listener-added', 'true');
        autoAssignBtn.removeAttribute('onclick');
        autoAssignBtn.addEventListener('click', function() {
            autoAssign();
        });
    }

    // Export button
    const exportBtn = document.querySelector('button[onclick="exportTasks()"]');
    if (exportBtn && !exportBtn.hasAttribute('data-listener-added')) {
        exportBtn.setAttribute('data-listener-added', 'true');
        exportBtn.removeAttribute('onclick');
        exportBtn.addEventListener('click', function() {
            exportTasks();
        });
    }

    // Gantt view controls (if in project view)
    const ganttProductSelect = document.getElementById('ganttProductSelect');
    if (ganttProductSelect) {
        ganttProductSelect.addEventListener('change', function() {
            if (typeof renderGanttChart === 'function') {
                renderGanttChart();
            }
        });
    }

    const ganttTeamSelect = document.getElementById('ganttTeamSelect');
    if (ganttTeamSelect) {
        ganttTeamSelect.addEventListener('change', function() {
            if (typeof renderGanttChart === 'function') {
                renderGanttChart();
            }
        });
    }

    const ganttSortSelect = document.getElementById('ganttSortSelect');
    if (ganttSortSelect) {
        ganttSortSelect.addEventListener('change', function() {
            if (typeof handleGanttSortChange === 'function') {
                handleGanttSortChange();
            }
        });
    }

    // Task assignment selects (dynamic)
    document.addEventListener('change', function(e) {
        if (e.target.classList.contains('assign-select')) {
            const taskId = e.target.dataset.taskId;
            const position = e.target.dataset.position || '0';
            const mechanicId = e.target.value;

            // Initialize saved assignments for this scenario if needed
            if (!savedAssignments[currentScenario]) {
                savedAssignments[currentScenario] = {};
            }

            // Update or create assignment record
            if (!savedAssignments[currentScenario][taskId]) {
                const task = scenarioData.tasks.find(t => t.taskId === taskId);
                if (task) {
                    savedAssignments[currentScenario][taskId] = {
                        mechanics: [],
                        team: task.team,
                        mechanicsNeeded: task.mechanics || 1
                    };
                }
            }

            // Update the specific position
            if (savedAssignments[currentScenario][taskId]) {
                const assignment = savedAssignments[currentScenario][taskId];
                if (!assignment.mechanics) assignment.mechanics = [];

                // Ensure array is large enough
                while (assignment.mechanics.length <= parseInt(position)) {
                    assignment.mechanics.push('');
                }

                // Set the mechanic at this position
                assignment.mechanics[parseInt(position)] = mechanicId;

                // Mark as partial if not all positions filled
                const filledCount = assignment.mechanics.filter(m => m).length;
                assignment.partial = filledCount < assignment.mechanicsNeeded;
            }

            // Visual feedback
            if (mechanicId) {
                e.target.style.backgroundColor = '#d4edda';
                setTimeout(() => {
                    e.target.style.backgroundColor = '';
                    e.target.classList.add('has-saved-assignment');
                }, 1000);

                // Update mechanic schedules for Individual view
                updateMechanicSchedulesFromAssignments();
            } else {
                e.target.classList.remove('has-saved-assignment');
            }

            // Optional: Make API call to save on server
            if (mechanicId) {
                fetch('/api/assign_task', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        taskId: taskId,
                        mechanicId: mechanicId,
                        position: position,
                        scenario: currentScenario
                    })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        console.log(`Task ${taskId} position ${position} assigned to ${mechanicId}`);
                    }
                })
                .catch(error => {
                    console.error('Error saving assignment:', error);
                });
            }
        }
    });

// Helper function to rebuild mechanic schedules from current assignments
function updateMechanicSchedulesFromAssignments() {
    if (!savedAssignments[currentScenario]) return;

    // Initialize mechanic schedules
    if (!savedAssignments[currentScenario].mechanicSchedules) {
        savedAssignments[currentScenario].mechanicSchedules = {};
    }

    // Clear existing schedules
    savedAssignments[currentScenario].mechanicSchedules = {};

    // Rebuild from task assignments
    for (const [taskId, assignment] of Object.entries(savedAssignments[currentScenario])) {
        if (taskId === 'mechanicSchedules') continue; // Skip the schedules object itself

        const task = scenarioData.tasks.find(t => t.taskId === taskId);
        if (!task) continue;

        // Add task to each assigned mechanic's schedule
        assignment.mechanics.forEach((mechanicId, position) => {
            if (!mechanicId) return;

            if (!savedAssignments[currentScenario].mechanicSchedules[mechanicId]) {
                // Extract display name from team and position
                const teamMatch = mechanicId.match(/(.+)_(\d+)$/);
                let displayName = mechanicId;
                if (teamMatch) {
                    const team = teamMatch[1];
                    const position = teamMatch[2];
                    const isQuality = team.toLowerCase().includes('quality');
                    displayName = `${isQuality ? 'Inspector' : 'Mechanic'} #${position} - ${team}`;
                }

                savedAssignments[currentScenario].mechanicSchedules[mechanicId] = {
                    mechanicId: mechanicId,
                    displayName: displayName,
                    team: assignment.team,
                    tasks: []
                };
            }

            // Add task to mechanic's schedule
            savedAssignments[currentScenario].mechanicSchedules[mechanicId].tasks.push({
                taskId: taskId,
                startTime: task.startTime,
                endTime: task.endTime,
                type: task.type,
                product: task.product,
                duration: task.duration
            });
        });
    }

    // Sort tasks by start time for each mechanic
    for (const mechanicId in savedAssignments[currentScenario].mechanicSchedules) {
        savedAssignments[currentScenario].mechanicSchedules[mechanicId].tasks.sort((a, b) =>
            new Date(a.startTime) - new Date(b.startTime)
        );
    }
}

    // Keyboard shortcuts
    document.addEventListener('keydown', function(e) {
        // Ctrl/Cmd + R to refresh data
        if ((e.ctrlKey || e.metaKey) && e.key === 'r') {
            e.preventDefault();
            refreshData();
        }

        // Ctrl/Cmd + E to export
        if ((e.ctrlKey || e.metaKey) && e.key === 'e') {
            e.preventDefault();
            exportTasks();
        }

        // Number keys 1-4 to switch views
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
            switch(e.key) {
                case '1':
                    if (document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'SELECT') {
                        switchView('team-lead');
                    }
                    break;
                case '2':
                    if (document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'SELECT') {
                        switchView('management');
                    }
                    break;
                case '3':
                    if (document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'SELECT') {
                        switchView('mechanic');
                    }
                    break;
                case '4':
                    if (document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'SELECT') {
                        switchView('project');
                    }
                    break;
            }
        }
    });

    // Window resize handler for responsive adjustments
    let resizeTimeout;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(function() {
            // Refresh Gantt chart if visible
            if (currentView === 'project' && typeof renderGanttChart === 'function') {
                renderGanttChart();
            }
        }, 250);
    });

    // Handle browser back/forward buttons
    window.addEventListener('popstate', function(e) {
        if (e.state && e.state.view) {
            switchView(e.state.view);
        }
        if (e.state && e.state.scenario) {
            switchScenario(e.state.scenario);
        }
    });
}

// Setup product filter (new feature)
function setupProductFilter() {
    const teamFilters = document.querySelector('.team-filters');
    if (teamFilters && !document.getElementById('productSelect')) {
        const productFilter = document.createElement('div');
        productFilter.className = 'filter-group';
        productFilter.innerHTML = `
            <label>Product:</label>
            <select id="productSelect">
                <option value="all">All Products</option>
            </select>
        `;
        teamFilters.appendChild(productFilter);

        document.getElementById('productSelect').addEventListener('change', function() {
            selectedProduct = this.value;
            updateTeamLeadView();
        });
    }
}

// Switch scenario with enhanced handling
function switchScenario(scenario) {
    if (allScenarios[scenario]) {
        currentScenario = scenario;
        scenarioData = allScenarios[scenario];

        console.log(`Switched to ${scenario}, teamCapacities:`, scenarioData.teamCapacities);

        // CRITICAL: Re-populate team dropdowns with new scenario's capacities
        populateTeamDropdowns();

        updateProductFilter();
        showScenarioInfo();
        updateView();

        // Load saved assignments for this scenario if they exist
        if (currentView === 'team-lead') {
            loadSavedAssignments();
        }
    }
}

// Update product filter dropdown
function updateProductFilter() {
    const productSelect = document.getElementById('productSelect');
    if (productSelect && scenarioData.products) {
        const currentSelection = productSelect.value;
        productSelect.innerHTML = '<option value="all">All Products</option>';
        scenarioData.products.forEach(product => {
            const option = document.createElement('option');
            option.value = product.name;
            option.textContent = `${product.name} (${product.totalTasks} tasks)`;
            productSelect.appendChild(option);
        });
        if ([...productSelect.options].some(opt => opt.value === currentSelection)) {
            productSelect.value = currentSelection;
        } else {
            productSelect.value = 'all';
            selectedProduct = 'all';
        }
    }
}

// Show scenario-specific information
function showScenarioInfo() {
    let infoBanner = document.getElementById('scenarioInfo');
    if (!infoBanner) {
        const mainContent = document.querySelector('.main-content');
        infoBanner = document.createElement('div');
        infoBanner.id = 'scenarioInfo';
        infoBanner.style.cssText = 'background: #f0f9ff; border: 1px solid #3b82f6; border-radius: 8px; padding: 12px; margin-bottom: 20px;';
        mainContent.insertBefore(infoBanner, mainContent.firstChild);
    }

    let infoHTML = `<strong>${currentScenario.toUpperCase()}</strong>: `;
    if (currentScenario === 'scenario3' && scenarioData.achievedMaxLateness !== undefined) {
        if (scenarioData.achievedMaxLateness === 0) {
            infoHTML += `✓ Achieved zero lateness with ${scenarioData.totalWorkforce} workers`;
        } else {
            infoHTML += `Minimum achievable lateness: ${scenarioData.achievedMaxLateness} days (${scenarioData.totalWorkforce} workers)`;
        }
    } else if (currentScenario === 'scenario2') {
        infoHTML += `Optimal uniform capacity: ${scenarioData.optimalMechanics || 'N/A'} mechanics, ${scenarioData.optimalQuality || 'N/A'} quality per team`;
    } else {
        infoHTML += `Workforce: ${scenarioData.totalWorkforce}, Makespan: ${scenarioData.makespan} days`;
    }
    infoBanner.innerHTML = infoHTML;
}

// Switch between views
function switchView(view) {
    document.querySelectorAll('.view-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.view-content').forEach(v => v.classList.remove('active'));
    document.querySelector(`[data-view="${view}"]`).classList.add('active');
    document.getElementById(`${view}-view`).classList.add('active');
    currentView = view;
    updateView();
}

// Update view based on current selection
function updateView() {
    if (!scenarioData) return;
    if (currentView === 'team-lead') {
        updateTeamLeadView();
    } else if (currentView === 'management') {
        updateManagementView();
    } else if (currentView === 'mechanic') {
        updateMechanicView();
    } else if (currentView === 'project') {
        setupGanttProductFilter();
        setupGanttTeamFilter();
        renderGanttChart();
    }
}

function populateTeamDropdowns() {
    console.log(`Populating team dropdowns for scenario: ${currentScenario}`);

    if (!scenarioData || !scenarioData.teamCapacities) {
        console.warn('No team capacity data available in current scenario');
        return;
    }

    // Get teams from the CURRENT SCENARIO's teamCapacities
    const teamCapacities = scenarioData.teamCapacities;
    const allTeams = Object.keys(teamCapacities);

    // Debug: Log what capacities we're actually using
    console.log(`Loading ${allTeams.length} teams from ${currentScenario}:`);

    // Show a sample to verify we have the right capacities
    if (allTeams.length > 0) {
        const sampleTeam = allTeams[0];
        console.log(`  Sample - ${sampleTeam}: ${teamCapacities[sampleTeam]} capacity`);

        // Calculate total to verify scenario
        const totalCapacity = Object.values(teamCapacities).reduce((a, b) => a + b, 0);
        console.log(`  Total workforce for ${currentScenario}: ${totalCapacity}`);
    }

    // Separate mechanic and quality teams
    const mechanicTeams = allTeams.filter(team =>
        team.toLowerCase().includes('mechanic') ||
        team.toLowerCase().includes('mech')
    ).sort();

    const qualityTeams = allTeams.filter(team =>
        team.toLowerCase().includes('quality') ||
        team.toLowerCase().includes('qual')
    ).sort();

    // Update main team select dropdown
    const teamSelect = document.getElementById('teamSelect');
    if (teamSelect) {
        // Store current selection before clearing
        const currentSelection = teamSelect.value;

        teamSelect.innerHTML = `
            <option value="all">All Teams</option>
            <option value="all-mechanics">All Mechanic Teams</option>
            <option value="all-quality">All Quality Teams</option>
            <optgroup label="──────────"></optgroup>
        `;

        // Add mechanic teams with scenario-specific capacities
        if (mechanicTeams.length > 0) {
            const mechanicGroup = document.createElement('optgroup');
            mechanicGroup.label = 'Mechanic Teams';
            mechanicTeams.forEach(team => {
                const option = document.createElement('option');
                option.value = team;
                option.textContent = `${team} (${teamCapacities[team]} capacity)`;
                mechanicGroup.appendChild(option);
            });
            teamSelect.appendChild(mechanicGroup);
        }

        // Add quality teams with scenario-specific capacities
        if (qualityTeams.length > 0) {
            const qualityGroup = document.createElement('optgroup');
            qualityGroup.label = 'Quality Teams';
            qualityTeams.forEach(team => {
                const option = document.createElement('option');
                option.value = team;
                option.textContent = `${team} (${teamCapacities[team]} capacity)`;
                qualityGroup.appendChild(option);
            });
            teamSelect.appendChild(qualityGroup);
        }

        // Restore previous selection if it still exists
        const options = Array.from(teamSelect.options);
        if (options.some(opt => opt.value === currentSelection)) {
            teamSelect.value = currentSelection;
        } else {
            teamSelect.value = 'all';
            selectedTeam = 'all';
        }
    }

    // Log summary of what was loaded
    console.log(`Populated dropdowns with ${allTeams.length} teams from ${currentScenario}:`);
    console.log(`  - ${mechanicTeams.length} mechanic teams`);
    console.log(`  - ${qualityTeams.length} quality teams`);

    // Extra debug for scenario 3 to verify optimized capacities
    if (currentScenario === 'scenario3' && mechanicTeams.length > 0) {
        console.log('Scenario 3 mechanic capacities:',
            mechanicTeams.map(t => `${t}: ${teamCapacities[t]}`).join(', '));
    }
        // At the end of the function, add:
    // Update shift dropdown to match selected team
    updateShiftDropdown();
}

function updateShiftDropdown() {
    const shiftSelect = document.getElementById('shiftSelect');
    if (!shiftSelect || !scenarioData) return;

    // Store current selection
    const currentShiftSelection = shiftSelect.value;

    // Get available shifts based on selected team(s)
    let availableShifts = new Set();

    if (!scenarioData.teamShifts) {
        console.warn('No team shift data available');
        // Default to all shifts if no data
        availableShifts.add('1st');
        availableShifts.add('2nd');
        availableShifts.add('3rd');
    } else {
        if (selectedTeam === 'all') {
            // All teams - get all shifts from all teams
            Object.values(scenarioData.teamShifts).forEach(shifts => {
                if (Array.isArray(shifts)) {
                    shifts.forEach(shift => availableShifts.add(shift));
                }
            });
        } else if (selectedTeam === 'all-mechanics') {
            // All mechanic teams - get shifts from mechanic teams only
            Object.entries(scenarioData.teamShifts).forEach(([team, shifts]) => {
                if (team.toLowerCase().includes('mechanic') || team.toLowerCase().includes('mech')) {
                    if (Array.isArray(shifts)) {
                        shifts.forEach(shift => availableShifts.add(shift));
                    }
                }
            });
        } else if (selectedTeam === 'all-quality') {
            // All quality teams - get shifts from quality teams only
            Object.entries(scenarioData.teamShifts).forEach(([team, shifts]) => {
                if (team.toLowerCase().includes('quality') || team.toLowerCase().includes('qual')) {
                    if (Array.isArray(shifts)) {
                        shifts.forEach(shift => availableShifts.add(shift));
                    }
                }
            });
        } else {
            // Specific team selected - get only that team's shifts
            const teamShifts = scenarioData.teamShifts[selectedTeam];
            if (Array.isArray(teamShifts)) {
                teamShifts.forEach(shift => availableShifts.add(shift));
            } else {
                // Fallback to all shifts if team not found
                console.warn(`No shift data for team: ${selectedTeam}`);
                availableShifts.add('1st');
                availableShifts.add('2nd');
                availableShifts.add('3rd');
            }
        }
    }

    // If no shifts found, default to all
    if (availableShifts.size === 0) {
        availableShifts.add('1st');
        availableShifts.add('2nd');
        availableShifts.add('3rd');
    }

    // Sort shifts in order (1st, 2nd, 3rd)
    const shiftOrder = ['1st', '2nd', '3rd'];
    const sortedShifts = Array.from(availableShifts).sort((a, b) => {
        return shiftOrder.indexOf(a) - shiftOrder.indexOf(b);
    });

    // Rebuild shift dropdown
    shiftSelect.innerHTML = '<option value="all">All Shifts</option>';

    sortedShifts.forEach(shift => {
        const option = document.createElement('option');
        option.value = shift;

        // Add shift times for clarity
        let shiftLabel = shift + ' Shift';
        if (shift === '1st') {
            shiftLabel += ' (6:00 AM - 2:30 PM)';
        } else if (shift === '2nd') {
            shiftLabel += ' (2:30 PM - 11:00 PM)';
        } else if (shift === '3rd') {
            shiftLabel += ' (11:00 PM - 6:00 AM)';
        }

        option.textContent = shiftLabel;
        shiftSelect.appendChild(option);
    });

    // Restore selection if still available
    const newOptions = Array.from(shiftSelect.options);
    if (newOptions.some(opt => opt.value === currentShiftSelection)) {
        shiftSelect.value = currentShiftSelection;
    } else {
        shiftSelect.value = 'all';
        selectedShift = 'all';
    }

    // Log what shifts are available
    console.log(`Updated shift dropdown for ${selectedTeam}:`, Array.from(availableShifts));
}

// Enhanced Team Lead View with product-specific filtering
async function updateTeamLeadView() {
    if (!scenarioData) return;

    // Determine which teams to include based on selection
    let teamsToInclude = [];

    if (selectedTeam === 'all') {
        teamsToInclude = Object.keys(scenarioData.teamCapacities || {});
    } else if (selectedTeam === 'all-mechanics') {
        teamsToInclude = Object.keys(scenarioData.teamCapacities || {})
            .filter(t => t.toLowerCase().includes('mechanic') || t.toLowerCase().includes('mech'));
    } else if (selectedTeam === 'all-quality') {
        teamsToInclude = Object.keys(scenarioData.teamCapacities || {})
            .filter(t => t.toLowerCase().includes('quality') || t.toLowerCase().includes('qual'));
    } else {
        teamsToInclude = [selectedTeam];
    }

    // Calculate total capacity for selected teams
    const teamCap = teamsToInclude.reduce((sum, team) =>
        sum + (scenarioData.teamCapacities[team] || 0), 0
    );

    document.getElementById('teamCapacity').textContent = teamCap;

    // Filter tasks for selected teams
    let tasks = (scenarioData.tasks || []).filter(task => {
        const teamMatch = teamsToInclude.includes(task.team);
        const shiftMatch = selectedShift === 'all' || task.shift === selectedShift;
        const productMatch = selectedProduct === 'all' || task.product === selectedProduct;
        return teamMatch && shiftMatch && productMatch;
    });

    // Count task types
    const taskTypeCounts = {};
    tasks.forEach(task => {
        taskTypeCounts[task.type] = (taskTypeCounts[task.type] || 0) + 1;
    });

    // Update task counts for today
    const today = new Date();
    const todayTasks = tasks.filter(t => {
        const taskDate = new Date(t.startTime);
        return taskDate.toDateString() === today.toDateString();
    });
    document.getElementById('tasksToday').textContent = todayTasks.length;

    // Count late parts and rework
    const latePartTasks = tasks.filter(t => t.isLatePartTask).length;
    const reworkTasks = tasks.filter(t => t.isReworkTask).length;

    // Calculate utilization for selected teams
    let util = 0;
    if (selectedTeam === 'all') {
        util = scenarioData.avgUtilization || 0;
    } else if (selectedTeam === 'all-mechanics' || selectedTeam === 'all-quality') {
        const groupUtilizations = teamsToInclude
            .map(team => scenarioData.utilization && scenarioData.utilization[team] || 0)
            .filter(u => u > 0);
        util = groupUtilizations.length > 0
            ? groupUtilizations.reduce((a, b) => a + b) / groupUtilizations.length
            : 0;
    } else {
        util = (scenarioData.utilization && scenarioData.utilization[selectedTeam]) || 0;
    }
    document.getElementById('teamUtilization').textContent = Math.round(util) + '%';

    // Update critical tasks count
    const critical = tasks.filter(t =>
        t.priority <= 10 ||
        t.isLatePartTask ||
        t.isReworkTask ||
        t.isCritical ||
        (t.slackHours !== undefined && t.slackHours < 24)
    ).length;
    document.getElementById('criticalTasks').textContent = critical;

    // Update task table WITH MULTIPLE DROPDOWNS FOR MULTI-MECHANIC TASKS
    const tbody = document.getElementById('taskTableBody');
    tbody.innerHTML = '';

    // Sort tasks by start time and show top 30
    tasks.sort((a, b) => new Date(a.startTime) - new Date(b.startTime));
    tasks.slice(0, 30).forEach(task => {
        const row = tbody.insertRow();
        const startTime = new Date(task.startTime);
        const mechanicsNeeded = task.mechanics || 1;

        // Add special indicators
        let typeIndicator = '';
        if (task.isLatePartTask) typeIndicator = ' 🔦';
        else if (task.isReworkTask) typeIndicator = ' 🔧';
        else if (task.isCritical) typeIndicator = ' ⚡';

        // Show dependencies if any
        let dependencyInfo = '';
        if (task.dependencies && task.dependencies.length > 0) {
            const deps = task.dependencies.slice(0, 3).map(d =>
                typeof d === 'object' ? (d.taskId || d.id || d.task) : d
            ).join(', ');
            const more = task.dependencies.length > 3 ? ` +${task.dependencies.length - 3} more` : '';
            dependencyInfo = `<span style="color: #6b7280; font-size: 11px;">Deps: ${deps}${more}</span>`;
        }

        // Generate assignment cells based on mechanics needed
        let assignmentCells = '';
        if (mechanicsNeeded === 1) {
            // Single dropdown for single mechanic
            assignmentCells = `
                <td>
                    <select class="assign-select" data-task-id="${task.taskId}" data-position="0">
                        <option value="">Unassigned</option>
                        ${generateMechanicOptions(teamsToInclude)}
                    </select>
                </td>
            `;
        } else {
            // Multiple dropdowns for multiple mechanics
            assignmentCells = `
                <td>
                    <div style="display: flex; flex-direction: column; gap: 5px;">
                        ${Array.from({length: mechanicsNeeded}, (_, i) => `
                            <select class="assign-select" data-task-id="${task.taskId}" data-position="${i}" style="width: 100%; font-size: 12px;">
                                <option value="">Mechanic ${i + 1}</option>
                                ${generateMechanicOptions(teamsToInclude)}
                            </select>
                        `).join('')}
                    </div>
                </td>
            `;
        }

        row.innerHTML = `
            <td class="priority">${task.priority || '-'}</td>
            <td class="task-id">${task.taskId}${typeIndicator}</td>
            <td><span class="task-type ${getTaskTypeClass(task.type)}">${task.type}</span></td>
            <td>${task.product}<br>${dependencyInfo}</td>
            <td>${formatDateTime(startTime)}</td>
            <td>${task.duration} min</td>
            <td style="text-align: center;">${mechanicsNeeded}</td>
            ${assignmentCells}
        `;

        // Highlight special rows
        if (task.isLatePartTask) {
            row.style.backgroundColor = '#fef3c7';
        } else if (task.isReworkTask) {
            row.style.backgroundColor = '#fee2e2';
        } else if (task.isCritical) {
            row.style.backgroundColor = '#dbeafe';
        }
    });

    // Add task type summary
    updateTaskTypeSummary(taskTypeCounts, latePartTasks, reworkTasks);

    // Update status message to show what's selected
    updateSelectionStatus(teamsToInclude);

    // Load saved assignments if they exist
    loadSavedAssignments();
}

// Removed unused helper functions - functionality is now integrated into autoAssign()

// Helper function to generate mechanic options based on selected teams
function generateMechanicOptions(teamsToInclude) {
    let options = '';

    teamsToInclude.forEach(team => {
        const capacity = (scenarioData.teamCapacities && scenarioData.teamCapacities[team]) || 0;
        const isQuality = team.toLowerCase().includes('quality');

        for (let i = 1; i <= capacity; i++) {
            const mechId = `${team}_${i}`; // Format: "Mechanic Team 1_1"
            const label = `${isQuality ? 'Inspector' : 'Mechanic'} #${i} - ${team}`;
            options += `<option value="${mechId}">${label}</option>`;
        }
    });

    return options;
}

// Helper function to update selection status
function updateSelectionStatus(teamsToInclude) {
    let statusText = '';

    if (selectedTeam === 'all') {
        statusText = `Showing all teams (${teamsToInclude.length} teams)`;
    } else if (selectedTeam === 'all-mechanics') {
        statusText = `Showing all mechanic teams (${teamsToInclude.length} teams)`;
    } else if (selectedTeam === 'all-quality') {
        statusText = `Showing all quality teams (${teamsToInclude.length} teams)`;
    } else {
        statusText = `Showing ${selectedTeam}`;
    }

    // Create or update status div
    let statusDiv = document.getElementById('teamSelectionStatus');
    if (!statusDiv) {
        statusDiv = document.createElement('div');
        statusDiv.id = 'teamSelectionStatus';
        statusDiv.style.cssText = `
            background: #E0F2FE;
            border: 1px solid #0284C7;
            border-radius: 6px;
            padding: 8px 12px;
            margin-bottom: 15px;
            font-size: 13px;
            color: #075985;
        `;
        const filtersDiv = document.querySelector('.team-filters');
        if (filtersDiv) {
            filtersDiv.parentNode.insertBefore(statusDiv, filtersDiv.nextSibling);
        }
    }

    statusDiv.innerHTML = `
        <strong>Filter Status:</strong> ${statusText} |
        Shift: ${selectedShift === 'all' ? 'All shifts' : selectedShift} |
        Product: ${selectedProduct === 'all' ? 'All products' : selectedProduct}
    `;
}

// Update task type summary
function updateTaskTypeSummary(taskTypeCounts, latePartCount, reworkCount) {
    let summaryDiv = document.getElementById('taskTypeSummary');
    if (!summaryDiv) {
        const statsContainer = document.querySelector('.team-stats');
        if (statsContainer) {
            summaryDiv = document.createElement('div');
            summaryDiv.id = 'taskTypeSummary';
            summaryDiv.className = 'stat-card';
            summaryDiv.style.gridColumn = 'span 2';
            statsContainer.appendChild(summaryDiv);
        }
    }

    if (summaryDiv) {
        let summaryHTML = '<h3>Task Type Breakdown</h3><div style="display: flex; gap: 15px; margin-top: 10px; flex-wrap: wrap;">';

        for (const [type, count] of Object.entries(taskTypeCounts)) {
            const color = getTaskTypeColor(type);
            summaryHTML += `
                <div style="flex: 1; min-width: 100px;">
                    <div style="font-size: 18px; font-weight: bold; color: ${color};">${count}</div>
                    <div style="font-size: 11px; color: #6b7280;">${type}</div>
                </div>
            `;
        }

        summaryHTML += '</div>';

        if (latePartCount > 0 || reworkCount > 0) {
            summaryHTML += '<div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #e5e7eb;">';
            summaryHTML += `<span style="margin-right: 15px;">🔦 Late Parts: ${latePartCount}</span>`;
            summaryHTML += `<span>🔧 Rework: ${reworkCount}</span>`;
            summaryHTML += '</div>';
        }

        summaryDiv.innerHTML = summaryHTML;
    }
}

// Update task type summary (new feature)
function updateTaskTypeSummary(taskTypeCounts, latePartCount, reworkCount) {
    let summaryDiv = document.getElementById('taskTypeSummary');
    if (!summaryDiv) {
        const statsContainer = document.querySelector('.team-stats');
        if (statsContainer) {
            summaryDiv = document.createElement('div');
            summaryDiv.id = 'taskTypeSummary';
            summaryDiv.className = 'stat-card';
            summaryDiv.style.gridColumn = 'span 2';
            statsContainer.appendChild(summaryDiv);
        }
    }

    if (summaryDiv) {
        let summaryHTML = '<h3>Task Type Breakdown</h3><div style="display: flex; gap: 15px; margin-top: 10px;">';
        for (const [type, count] of Object.entries(taskTypeCounts)) {
            const color = getTaskTypeColor(type);
            summaryHTML += `
                <div style="flex: 1;">
                    <div style="font-size: 18px; font-weight: bold; color: ${color};">${count}</div>
                    <div style="font-size: 11px; color: #6b7280;">${type}</div>
                </div>
            `;
        }
        summaryHTML += '</div>';
        if (latePartCount > 0 || reworkCount > 0) {
            summaryHTML += '<div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #e5e7eb;">';
            summaryHTML += `<span style="margin-right: 15px;">🔦 Late Parts: ${latePartCount}</span>`;
            summaryHTML += `<span>🔧 Rework: ${reworkCount}</span>`;
            summaryHTML += '</div>';
        }
        summaryDiv.innerHTML = summaryHTML;
    }
}

// Enhanced Management View with lateness metrics
function updateManagementView() {
    if (!scenarioData) return;
    document.getElementById('totalWorkforce').textContent = scenarioData.totalWorkforce;
    document.getElementById('makespan').textContent = scenarioData.makespan;
    document.getElementById('onTimeRate').textContent = scenarioData.onTimeRate + '%';
    document.getElementById('avgUtilization').textContent = scenarioData.avgUtilization + '%';

    let latenessCard = document.getElementById('latenessMetrics');
    if (!latenessCard) {
        const metricsGrid = document.querySelector('.metrics-grid');
        if (metricsGrid) {
            latenessCard = document.createElement('div');
            latenessCard.className = 'metric-card';
            latenessCard.id = 'latenessMetrics';
            metricsGrid.appendChild(latenessCard);
        }
    }

    if (latenessCard) {
        let latenessHTML = '<h3>Lateness Metrics</h3>';
        if (scenarioData.achievedMaxLateness !== undefined) {
            latenessHTML += `<div class="metric-value">${scenarioData.achievedMaxLateness}</div>`;
            latenessHTML += '<div class="metric-label">days max lateness (achieved)</div>';
        } else {
            latenessHTML += `<div class="metric-value">${scenarioData.maxLateness || 0}</div>`;
            latenessHTML += '<div class="metric-label">days maximum lateness</div>';
        }
        latenessCard.innerHTML = latenessHTML;
    }

    const productGrid = document.getElementById('productGrid');
    productGrid.innerHTML = '';
    scenarioData.products.forEach(product => {
        const status = product.onTime ? 'on-time' :
            product.latenessDays <= 5 ? 'at-risk' : 'late';
        const card = document.createElement('div');
        card.className = 'product-card';
        card.innerHTML = `
            <div class="product-header">
                <div class="product-name">${product.name}</div>
                <div class="status-badge ${status}">${status.replace('-', ' ')}</div>
            </div>
            <div class="progress-bar">
                <div class="progress-fill" style="width: ${product.progress}%"></div>
            </div>
            <div class="product-stats">
                <span>📅 ${product.daysRemaining} days remaining</span>
                <span>⚡ ${product.criticalPath} critical tasks</span>
            </div>
            <div class="product-stats" style="margin-top: 5px; font-size: 11px;">
                <span>Tasks: ${product.totalTasks}</span>
                ${product.latePartsCount > 0 ? `<span>🔦 Late Parts: ${product.latePartsCount}</span>` : ''}
                ${product.reworkCount > 0 ? `<span>🔧 Rework: ${product.reworkCount}</span>` : ''}
            </div>
            ${product.latenessDays > 0 ? `
                <div style="margin-top: 8px; padding: 5px; background: #fee2e2; border-radius: 4px; font-size: 12px; text-align: center;">
                    Late by ${product.latenessDays} days
                </div>
            ` : ''}
        `;
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => showProductDetails(product.name));
        productGrid.appendChild(card);
    });

    const utilizationChart = document.getElementById('utilizationChart');
    utilizationChart.innerHTML = '';
    Object.entries(scenarioData.utilization).forEach(([team, utilization]) => {
        const item = document.createElement('div');
        item.className = 'utilization-item';
        let fillColor = 'linear-gradient(90deg, #10b981, #10b981)';
        if (utilization > 90) {
            fillColor = 'linear-gradient(90deg, #ef4444, #ef4444)';
        } else if (utilization > 75) {
            fillColor = 'linear-gradient(90deg, #f59e0b, #f59e0b)';
        }
        item.innerHTML = `
            <div class="team-label">${team}</div>
            <div class="utilization-bar">
                <div class="utilization-fill" style="width: ${utilization}%; background: ${fillColor};">
                    <span class="utilization-percent">${utilization}%</span>
                </div>
            </div>
        `;
        utilizationChart.appendChild(item);
    });
}

// Show product details (new feature)
async function showProductDetails(productName) {
    try {
        const response = await fetch(`/api/product/${productName}/tasks?scenario=${currentScenario}`);
        const data = await response.json();
        if (response.ok) {
            alert(`${productName}: ${data.totalTasks} total tasks\n` +
                `Production: ${data.taskBreakdown.Production || 0}\n` +
                `Quality: ${data.taskBreakdown['Quality Inspection'] || 0}\n` +
                `Late Parts: ${data.taskBreakdown['Late Part'] || 0}\n` +
                `Rework: ${data.taskBreakdown.Rework || 0}`);
        }
    } catch (error) {
        console.error('Error loading product details:', error);
    }
}

// Enhanced Individual Mechanic View with actual assignments
async function updateMechanicView() {
    if (!scenarioData) return;

    // First, populate the mechanic dropdown with actual mechanics based on team capacities
    const mechanicSelect = document.getElementById('mechanicSelect');
    if (mechanicSelect) {
        const currentSelection = mechanicSelect.value;
        mechanicSelect.innerHTML = '';

        // Add option groups for each team
        const mechanicTeams = Object.keys(scenarioData.teamCapacities || {})
            .filter(team => team.toLowerCase().includes('mechanic') || team.toLowerCase().includes('mech'))
            .sort();

        const qualityTeams = Object.keys(scenarioData.teamCapacities || {})
            .filter(team => team.toLowerCase().includes('quality') || team.toLowerCase().includes('qual'))
            .sort();

        // Add mechanic teams
        if (mechanicTeams.length > 0) {
            const mechanicGroup = document.createElement('optgroup');
            mechanicGroup.label = 'Mechanic Teams';

            mechanicTeams.forEach(team => {
                const capacity = scenarioData.teamCapacities[team] || 0;
                for (let i = 1; i <= capacity; i++) {
                    const option = document.createElement('option');
                    option.value = `${team}_${i}`;
                    option.textContent = `Mechanic #${i} - ${team}`;
                    mechanicGroup.appendChild(option);
                }
            });

            mechanicSelect.appendChild(mechanicGroup);
        }

        // Add quality teams
        if (qualityTeams.length > 0) {
            const qualityGroup = document.createElement('optgroup');
            qualityGroup.label = 'Quality Teams';

            qualityTeams.forEach(team => {
                const capacity = scenarioData.teamCapacities[team] || 0;
                for (let i = 1; i <= capacity; i++) {
                    const option = document.createElement('option');
                    option.value = `${team}_${i}`;
                    option.textContent = `Inspector #${i} - ${team}`;
                    qualityGroup.appendChild(option);
                }
            });

            mechanicSelect.appendChild(qualityGroup);
        }

        // Restore selection if it still exists
        if ([...mechanicSelect.options].some(opt => opt.value === currentSelection)) {
            mechanicSelect.value = currentSelection;
        } else if (mechanicSelect.options.length > 0) {
            mechanicSelect.value = mechanicSelect.options[0].value;
        }
    }

    // Get selected mechanic
    const mechanicId = document.getElementById('mechanicSelect').value;
    if (!mechanicId) {
        document.getElementById('currentShift').textContent = 'No mechanic selected';
        document.getElementById('tasksAssigned').textContent = '0';
        document.getElementById('estCompletion').textContent = 'N/A';
        document.getElementById('mechanicTimeline').innerHTML = '<div style="padding: 20px; color: #6b7280;">No mechanic selected</div>';
        return;
    }

    // Update header with mechanic name
    const mechanicOption = document.querySelector(`#mechanicSelect option[value="${mechanicId}"]`);
    if (mechanicOption) {
        const headerElement = document.getElementById('mechanicName');
        if (headerElement) {
            headerElement.textContent = `Task Schedule for ${mechanicOption.textContent}`;
        }
    }

    // Check if we have saved assignments for this mechanic
    let mechanicTasks = [];
    let mechanicTeam = '';

    if (savedAssignments[currentScenario] && savedAssignments[currentScenario].mechanicSchedules) {
        const mechanicSchedule = savedAssignments[currentScenario].mechanicSchedules[mechanicId];
        if (mechanicSchedule) {
            mechanicTasks = mechanicSchedule.tasks || [];
            mechanicTeam = mechanicSchedule.team;
        }
    }

    // If no saved assignments, show empty schedule
    if (mechanicTasks.length === 0) {
        // Extract team from mechanicId (format: "Mechanic Team 1_1")
        const teamMatch = mechanicId.match(/(.+)_\d+$/);
        mechanicTeam = teamMatch ? teamMatch[1] : 'Unknown Team';

        document.getElementById('currentShift').textContent = '1st Shift';
        document.getElementById('tasksAssigned').textContent = '0';
        document.getElementById('estCompletion').textContent = 'No tasks assigned';

        const timeline = document.getElementById('mechanicTimeline');
        timeline.innerHTML = `
            <div style="padding: 20px; text-align: center; color: #6b7280;">
                <div style="font-size: 48px; margin-bottom: 10px;">📋</div>
                <div style="font-size: 16px; font-weight: 500;">No Tasks Assigned</div>
                <div style="font-size: 14px; margin-top: 5px;">Use the Team Lead view to assign tasks</div>
            </div>
        `;
        return;
    }

    // Display assigned tasks
    document.getElementById('currentShift').textContent = '1st Shift'; // Could be determined from tasks
    document.getElementById('tasksAssigned').textContent = mechanicTasks.length;

    // Calculate estimated completion time
    if (mechanicTasks.length > 0) {
        const lastTask = mechanicTasks[mechanicTasks.length - 1];
        const endTime = new Date(lastTask.endTime);
        document.getElementById('estCompletion').textContent = formatTime(endTime);
    } else {
        document.getElementById('estCompletion').textContent = 'No tasks';
    }

    // Build timeline
    const timeline = document.getElementById('mechanicTimeline');
    timeline.innerHTML = '';

    // Group tasks by date
    const tasksByDate = {};
    mechanicTasks.forEach(task => {
        const date = new Date(task.startTime).toDateString();
        if (!tasksByDate[date]) {
            tasksByDate[date] = [];
        }
        tasksByDate[date].push(task);
    });

    // Display tasks grouped by date
    Object.entries(tasksByDate).forEach(([date, tasks]) => {
        // Add date header
        const dateHeader = document.createElement('div');
        dateHeader.style.cssText = `
            background: #f3f4f6;
            padding: 8px 12px;
            font-weight: 600;
            color: #374151;
            margin: 10px 0 5px 0;
            border-radius: 6px;
        `;
        dateHeader.textContent = date;
        timeline.appendChild(dateHeader);

        // Add tasks for this date
        tasks.forEach(task => {
            const startTime = new Date(task.startTime);
            const item = document.createElement('div');
            item.className = 'timeline-item';

            // Color code by task type
            let borderColor = '#3b82f6'; // Default blue
            let typeIcon = '🔧';

            if (task.type === 'Quality Inspection') {
                borderColor = '#10b981';
                typeIcon = '✓';
            } else if (task.type === 'Late Part') {
                borderColor = '#f59e0b';
                typeIcon = '📦';
            } else if (task.type === 'Rework') {
                borderColor = '#ef4444';
                typeIcon = '🔄';
            }

            item.style.borderLeftColor = borderColor;

            item.innerHTML = `
                <div class="timeline-time">${formatTime(startTime)}</div>
                <div class="timeline-content">
                    <div class="timeline-task">
                        ${typeIcon} Task ${task.taskId} - ${task.type}
                    </div>
                    <div class="timeline-details">
                        <span>📦 ${task.product}</span>
                        <span>⏱️ ${task.duration} minutes</span>
                    </div>
                </div>
            `;
            timeline.appendChild(item);
        });
    });

    // Add summary at the bottom
    const summary = document.createElement('div');
    summary.style.cssText = `
        background: #eff6ff;
        border: 1px solid #3b82f6;
        border-radius: 8px;
        padding: 12px;
        margin-top: 20px;
        font-size: 13px;
    `;

    const totalMinutes = mechanicTasks.reduce((sum, task) => sum + task.duration, 0);
    const totalHours = Math.round(totalMinutes / 60 * 10) / 10;

    summary.innerHTML = `
        <strong>Daily Summary</strong><br>
        Total Tasks: ${mechanicTasks.length}<br>
        Total Work Time: ${totalHours} hours<br>
        Team: ${mechanicTeam}
    `;

    timeline.appendChild(summary);
}

// Helper functions
function getTaskTypeClass(type) {
    const typeMap = {
        'Production': 'production',
        'Quality Inspection': 'quality',
        'Late Part': 'late-part',
        'Rework': 'rework'
    };
    return typeMap[type] || 'production';
}

function getTaskTypeColor(type) {
    const colorMap = {
        'Production': '#10b981',
        'Quality Inspection': '#3b82f6',
        'Late Part': '#f59e0b',
        'Rework': '#ef4444'
    };
    return colorMap[type] || '#6b7280';
}

function formatTime(date) {
    return date.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

function formatDate(date) {
    return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
    });
}

// Gantt chart helpers
function getGanttColor(product, isCritical) {
    const productColors = {
        'Product A': 'gantt-prod-a',
        'Product B': 'gantt-prod-b',
        'Product C': 'gantt-prod-c',
        'Product D': 'gantt-prod-d',
        'Product E': 'gantt-prod-e'
    };
    let classes = '';
    if (productColors[product]) {
        classes += productColors[product];
    }
    if (isCritical) {
        classes += ' gantt-critical';
    }
    return classes.trim();
}

function getGanttTasks(productFilter = 'all', teamFilter = 'all') {
    if (!scenarioData || !scenarioData.tasks) return [];
    return scenarioData.tasks
        .filter(task =>
            (productFilter === 'all' || task.product === productFilter) &&
            (teamFilter === 'all' || task.team === teamFilter)
        )
        .map(task => ({
            id: task.taskId,
            name: `${task.team} [ Task ${task.taskId} ] ${task.type}`,
            start: task.startTime,
            end: task.endTime,
            progress: 100,
            custom_class: getGanttColor(task.product, task.isCriticalPath),
            dependencies: (task.dependencies || []).map(d => d.task).join(','),
        }));
}

let gantt;
function renderGanttChart() {
    const productFilter = document.getElementById('ganttProductSelect').value || 'all';
    const teamFilter = document.getElementById('ganttTeamSelect').value || 'all';
    const tasks = getGanttTasks(productFilter, teamFilter);
    const ganttDiv = document.getElementById('ganttChart');
    ganttDiv.innerHTML = '';
    if (tasks.length === 0) {
        ganttDiv.innerHTML = '<div style="color: #ef4444;">No tasks to display.</div>';
        return;
    }
    gantt = new Gantt(ganttDiv, tasks, {
        view_mode: 'Day'
    });
}

function setupGanttProductFilter() {
    const select = document.getElementById('ganttProductSelect');
    select.innerHTML = '<option value="all">All Products</option>';
    if (scenarioData.products) {
        scenarioData.products.forEach(product => {
            const option = document.createElement('option');
            option.value = product.name;
            option.textContent = product.name;
            select.appendChild(option);
        });
    }
    select.onchange = renderGanttChart;
}

function setupGanttTeamFilter() {
    const select = document.getElementById('ganttTeamSelect');
    select.innerHTML = '<option value="all">All Teams</option>';
    if (scenarioData.tasks) {
        const teams = [...new Set(scenarioData.tasks.map(task => task.team))];
        teams.forEach(team => {
            const option = document.createElement('option');
            option.value = team;
            option.textContent = team;
            select.appendChild(option);
        });
    }
    select.onchange = renderGanttChart;
}

// Loading and error states
function showLoading(message = 'Loading...') {
    const content = document.querySelector('.main-content');
    if (content) {
        const loadingDiv = document.createElement('div');
        loadingDiv.id = 'loadingIndicator';
        loadingDiv.className = 'loading';
        loadingDiv.innerHTML = `
            <div style="text-align: center;">
                <div class="spinner"></div>
                <div style="margin-top: 20px;">${message}</div>
            </div>
        `;
        content.appendChild(loadingDiv);
    }
}

function hideLoading() {
    const loadingDiv = document.getElementById('loadingIndicator');
    if (loadingDiv) {
        loadingDiv.remove();
    }
}

function showError(message) {
    const content = document.querySelector('.main-content');
    if (content) {
        content.innerHTML = `
            <div style="text-align: center; padding: 40px; color: #ef4444;">
                <h2>Error</h2>
                <p>${message}</p>
                <button onclick="location.reload()" class="btn btn-primary" style="margin-top: 20px;">
                    Reload Page
                </button>
            </div>
        `;
    }
}

function formatDateTime(date) {
    return date.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

// Auto-assign function with capacity limits and persistent storage
async function autoAssign() {
    // Get visible tasks from the table (these are already filtered)
    const taskRows = document.querySelectorAll('#taskTableBody tr');
    let successCount = 0;
    let conflictCount = 0;
    let partialCount = 0;

    // Initialize saved assignments for this scenario if not exists
    if (!savedAssignments[currentScenario]) {
        savedAssignments[currentScenario] = {};
    }

    // Build mechanic availability tracking based on current filter
    const mechanicAvailability = {};

    // Determine which teams to include based on current selection
    let teamsToInclude = [];
    if (selectedTeam === 'all') {
        teamsToInclude = Object.keys(scenarioData.teamCapacities || {});
    } else if (selectedTeam === 'all-mechanics') {
        teamsToInclude = Object.keys(scenarioData.teamCapacities || {})
            .filter(t => t.toLowerCase().includes('mechanic') || t.toLowerCase().includes('mech'));
    } else if (selectedTeam === 'all-quality') {
        teamsToInclude = Object.keys(scenarioData.teamCapacities || {})
            .filter(t => t.toLowerCase().includes('quality') || t.toLowerCase().includes('qual'));
    } else {
        teamsToInclude = [selectedTeam];
    }

    // Create mechanics for each team, respecting capacity limits
    const teamMechanicCounters = {};

    teamsToInclude.forEach(team => {
        const capacity = (scenarioData.teamCapacities && scenarioData.teamCapacities[team]) || 0;
        const isQuality = team.toLowerCase().includes('quality');
        teamMechanicCounters[team] = 1;

        for (let i = 1; i <= capacity; i++) {
            const mechId = `${team}_${i}`; // Format: "Mechanic Team 1_1"
            mechanicAvailability[mechId] = {
                id: mechId,
                team: team,
                displayName: `${isQuality ? 'Inspector' : 'Mechanic'} #${i} - ${team}`,
                busyUntil: null,
                assignedTasks: [],
                isQuality: isQuality,
                teamPosition: i // Position within the team (1, 2, 3, etc.)
            };
        }
    });

    console.log(`Created mechanics for teams:`, Object.keys(teamMechanicCounters));
    console.log(`Total mechanics available:`, Object.keys(mechanicAvailability).length);

    // Process each visible task row
    taskRows.forEach(row => {
        const taskId = row.querySelector('.task-id')?.textContent?.replace(/[🔦🔧⚡]/g, '').trim();
        if (!taskId) return;

        // Find the task data
        const task = scenarioData.tasks.find(t => t.taskId === taskId);
        if (!task) {
            console.warn(`Task ${taskId} not found in scenario data`);
            return;
        }

        const mechanicsNeeded = task.mechanics || 1;
        const taskStart = new Date(task.startTime);
        const taskEnd = new Date(task.endTime);

        // Find available mechanics from the same team (respecting capacity)
        const availableMechanics = [];
        for (const [mechId, mech] of Object.entries(mechanicAvailability)) {
            if (mech.team === task.team) {
                // Check if mechanic is available
                if (!mech.busyUntil || mech.busyUntil <= taskStart) {
                    availableMechanics.push(mech);
                    if (availableMechanics.length >= mechanicsNeeded) break;
                }
            }
        }

        // Assign mechanics to task
        const assignedMechanics = [];

        if (availableMechanics.length >= mechanicsNeeded) {
            // Full assignment possible
            for (let i = 0; i < mechanicsNeeded; i++) {
                const mech = availableMechanics[i];
                mech.busyUntil = taskEnd;
                mech.assignedTasks.push({
                    taskId: taskId,
                    startTime: task.startTime,
                    endTime: task.endTime,
                    type: task.type,
                    product: task.product,
                    duration: task.duration
                });
                assignedMechanics.push(mech.id);

                // Update the dropdown
                const selectElement = row.querySelector(`.assign-select[data-task-id="${taskId}"][data-position="${i}"]`) ||
                                    row.querySelector(`.assign-select[data-task-id="${taskId}"]`);
                if (selectElement) {
                    selectElement.value = mech.id;
                    selectElement.style.backgroundColor = '#d4edda';
                    setTimeout(() => {
                        selectElement.style.backgroundColor = '';
                    }, 2000);
                }
            }

            // Save the assignment
            savedAssignments[currentScenario][taskId] = {
                mechanics: assignedMechanics,
                team: task.team,
                mechanicsNeeded: mechanicsNeeded
            };

            successCount++;
            row.style.backgroundColor = '#f0fdf4';
        } else if (availableMechanics.length > 0) {
            // Partial assignment
            for (let i = 0; i < availableMechanics.length; i++) {
                const mech = availableMechanics[i];
                mech.busyUntil = taskEnd;
                mech.assignedTasks.push({
                    taskId: taskId,
                    startTime: task.startTime,
                    endTime: task.endTime,
                    type: task.type,
                    product: task.product,
                    duration: task.duration
                });
                assignedMechanics.push(mech.id);

                const selectElement = row.querySelector(`.assign-select[data-task-id="${taskId}"][data-position="${i}"]`);
                if (selectElement) {
                    selectElement.value = mech.id;
                    selectElement.style.backgroundColor = '#fff3cd';
                    setTimeout(() => {
                        selectElement.style.backgroundColor = '';
                    }, 2000);
                }
            }

            // Save partial assignment
            savedAssignments[currentScenario][taskId] = {
                mechanics: assignedMechanics,
                team: task.team,
                mechanicsNeeded: mechanicsNeeded,
                partial: true
            };

            partialCount++;
            row.style.backgroundColor = '#fffbeb';
        } else {
            // No mechanics available
            conflictCount++;
            row.style.backgroundColor = '#fef2f2';
        }

        // Clear row color after a delay
        setTimeout(() => {
            row.style.backgroundColor = '';
        }, 3000);
    });

    // Store assignments for the Individual view
    if (!savedAssignments[currentScenario].mechanicSchedules) {
        savedAssignments[currentScenario].mechanicSchedules = {};
    }

    // Build mechanic schedules for Individual view
    for (const [mechId, mech] of Object.entries(mechanicAvailability)) {
        if (mech.assignedTasks.length > 0) {
            savedAssignments[currentScenario].mechanicSchedules[mechId] = {
                mechanicId: mechId,
                displayName: mech.displayName,
                team: mech.team,
                tasks: mech.assignedTasks.sort((a, b) =>
                    new Date(a.startTime) - new Date(b.startTime)
                )
            };
        }
    }

    // Update assignment summary
    if (typeof updateAssignmentSummary === 'function') {
        updateAssignmentSummary();
    }

    // Show results
    alert(`Auto-Assignment Complete!\n\nFully Assigned: ${successCount}\nPartially Assigned: ${partialCount}\nConflicts: ${conflictCount}\n\nTotal: ${taskRows.length} tasks\n\nAssignments have been saved and will persist across filter changes.`);

    console.log('Saved assignments:', savedAssignments[currentScenario]);
}

// Load saved assignments into the table
function loadSavedAssignments() {
    if (!savedAssignments[currentScenario]) return;

    const assignments = savedAssignments[currentScenario];
    const taskRows = document.querySelectorAll('#taskTableBody tr');
    let loadedCount = 0;

    taskRows.forEach(row => {
        const taskId = row.querySelector('.task-id')?.textContent?.replace(/[🔦🔧⚡]/g, '').trim();
        if (!taskId || !assignments[taskId]) return;

        const taskAssignment = assignments[taskId];
        const selectElements = row.querySelectorAll('.assign-select');

        // Restore assignments to dropdowns
        taskAssignment.mechanics.forEach((mechId, index) => {
            if (selectElements[index]) {
                // Check if this mechanic option exists in the dropdown
                const optionExists = Array.from(selectElements[index].options)
                    .some(opt => opt.value === mechId);

                if (optionExists) {
                    selectElements[index].value = mechId;
                    selectElements[index].classList.add('has-saved-assignment');
                    loadedCount++;
                }
            }
        });
    });

    // Update summary
    if (typeof updateAssignmentSummary === 'function') {
        updateAssignmentSummary();
    }

    if (loadedCount > 0) {
        console.log(`Loaded ${loadedCount} saved assignments for ${currentScenario}`);
    }
}

// Save assignments to localStorage for persistence across sessions
function saveAssignmentsToStorage() {
    try {
        localStorage.setItem(`assignments_${currentScenario}`, JSON.stringify(savedAssignments[currentScenario]));
        alert('Assignments saved successfully!');
    } catch (e) {
        console.error('Failed to save assignments:', e);
        alert('Failed to save assignments to browser storage.');
    }
}

// Load assignments from localStorage
function loadAssignmentsFromStorage() {
    try {
        const stored = localStorage.getItem(`assignments_${currentScenario}`);
        if (stored) {
            savedAssignments[currentScenario] = JSON.parse(stored);
            loadSavedAssignments();
            alert('Previous assignments loaded successfully!');
        } else {
            alert('No saved assignments found for this scenario.');
        }
    } catch (e) {
        console.error('Failed to load assignments:', e);
        alert('Failed to load assignments from browser storage.');
    }
}

// Clear all saved assignments
function clearSavedAssignments() {
    if (confirm('This will clear all saved assignments for this scenario. Continue?')) {
        savedAssignments[currentScenario] = {};
        localStorage.removeItem(`assignments_${currentScenario}`);

        // Clear all dropdowns
        document.querySelectorAll('.assign-select').forEach(select => {
            select.value = '';
            select.classList.remove('has-saved-assignment');
        });

        alert('Saved assignments cleared.');

        if (typeof updateAssignmentSummary === 'function') {
            updateAssignmentSummary();
        }
    }
}

// Export tasks function
async function exportTasks() {
    try {
        // Export to CSV including assignments
        const tasks = scenarioData.tasks || [];
        const assignments = savedAssignments[currentScenario] || {};

        // Build CSV data
        let csvContent = "Task ID,Type,Product,Team,Start Time,End Time,Duration,Mechanics Needed,Assigned Mechanics\n";

        tasks.forEach(task => {
            const assignment = assignments[task.taskId];
            const assignedMechanics = assignment ? assignment.mechanics.join('; ') : 'Unassigned';

            csvContent += `"${task.taskId}","${task.type}","${task.product}","${task.team}",`;
            csvContent += `"${task.startTime}","${task.endTime}","${task.duration}","${task.mechanics || 1}",`;
            csvContent += `"${assignedMechanics}"\n`;
        });

        // Download the CSV
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.setAttribute('href', url);
        link.setAttribute('download', `assignments_${currentScenario}_${new Date().toISOString().slice(0, 10)}.csv`);
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        if (typeof showNotification === 'function') {
            showNotification('Assignments exported successfully!', 'success');
        } else {
            alert('Assignments exported successfully!');
        }
    } catch (error) {
        console.error('Export failed:', error);
        // Fallback to server export
        window.location.href = `/api/export/${currentScenario}`;
    }
}

// Refresh data
async function refreshData() {
    if (confirm('This will recalculate all scenarios. It may take a few minutes. Continue?')) {
        showLoading('Refreshing all scenarios...');
        try {
            const response = await fetch('/api/refresh', { method: 'POST' });
            const result = await response.json();

            if (result.success) {
                await loadAllScenarios();
                alert('All scenarios refreshed successfully!');
            } else {
                alert('Failed to refresh: ' + result.error);
            }
        } catch (error) {
            alert('Error refreshing data: ' + error.message);
        } finally {
            hideLoading();
        }
    }
}

// Clear all assignments (for current view only, doesn't clear saved)
function clearAllAssignments() {
    if (!confirm('This will clear all current assignments in the view. Continue?')) return;

    // Reset all dropdowns
    document.querySelectorAll('.assign-select').forEach(select => {
        select.value = '';
        select.classList.remove('assigned', 'conflict', 'partial', 'has-saved-assignment');
    });

    // Update summary
    if (typeof updateAssignmentSummary === 'function') {
        updateAssignmentSummary();
    }

    // Visual feedback
    if (typeof showNotification === 'function') {
        showNotification('Current view assignments cleared', 'info');
    } else {
        alert('Current view assignments cleared');
    }
}

// Add refresh button to header if not exists
function setupRefreshButton() {
    const controls = document.querySelector('.controls');
    if (controls && !document.getElementById('refreshBtn')) {
        const refreshBtn = document.createElement('button');
        refreshBtn.id = 'refreshBtn';
        refreshBtn.className = 'btn btn-secondary';
        refreshBtn.innerHTML = '🔄 Refresh Data';
        refreshBtn.onclick = refreshData;
        refreshBtn.style.marginLeft = '10px';
        controls.appendChild(refreshBtn);
    }
}

// View assignment report
function viewAssignmentReport() {
    if (typeof currentScenario !== 'undefined') {
        window.open(`/api/assignment_report/${currentScenario}`, '_blank');
    }
}

// Update assignment summary panel
function updateAssignmentSummary() {
    const rows = document.querySelectorAll('#taskTableBody tr');
    let total = 0, complete = 0, partial = 0, unassigned = 0;

    rows.forEach(row => {
        total++;
        const selects = row.querySelectorAll('.assign-select');
        const assigned = Array.from(selects).filter(s => s.value).length;
        const needed = selects.length;

        if (assigned === 0) {
            unassigned++;
            row.classList.remove('fully-assigned', 'partially-assigned');
        } else if (assigned < needed) {
            partial++;
            row.classList.remove('fully-assigned');
            row.classList.add('partially-assigned');
        } else {
            complete++;
            row.classList.add('fully-assigned');
            row.classList.remove('partially-assigned');
        }
    });

    // Update summary panel
    document.getElementById('summaryTotal').textContent = total;
    document.getElementById('summaryComplete').textContent = complete;
    document.getElementById('summaryPartial').textContent = partial;
    document.getElementById('summaryUnassigned').textContent = unassigned;

    // Update progress bar
    const progress = total > 0 ? (complete / total) * 100 : 0;
    document.getElementById('summaryProgress').style.width = progress + '%';

    // Show/hide panel
    const panel = document.getElementById('assignmentSummary');
    if (panel) {
        if (total > 0) {
            panel.classList.add('visible');
        } else {
            panel.classList.remove('visible');
        }
    }
}

// Show notification
function showNotification(message, type = 'success') {
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        background: ${type === 'success' ? '#10b981' : type === 'error' ? '#ef4444' : '#3b82f6'};
        color: white;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        z-index: 10000;
        animation: slideInRight 0.3s ease-out;
    `;
    notification.textContent = message;

    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.animation = 'slideOutRight 0.3s ease-out';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// Expose functions globally for HTML onclick handlers
window.autoAssign = autoAssign;
window.saveAssignmentsToStorage = saveAssignmentsToStorage;
window.loadAssignmentsFromStorage = loadAssignmentsFromStorage;
window.clearSavedAssignments = clearSavedAssignments;
window.clearAllAssignments = clearAllAssignments;
window.exportTasks = exportTasks;
window.viewAssignmentReport = viewAssignmentReport;
window.updateAssignmentSummary = updateAssignmentSummary;
window.showNotification = showNotification;