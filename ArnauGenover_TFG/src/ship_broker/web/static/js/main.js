// web/static/js/main.js
document.addEventListener('DOMContentLoaded', function() {
    // Global email configuration check
    checkEmailConfiguration();
    
    // Load vessels data
    const vesselsTable = document.getElementById('vessels-table');
    if (vesselsTable) {
        loadVessels();
    }

    // Load cargoes data
    const cargoesTable = document.getElementById('cargoes-table');
    if (cargoesTable) {
        loadCargoes();
    }
});

// Email configuration check
async function checkEmailConfiguration() {
    try {
        const response = await fetch('/api/v1/email-stats');
        if (!response.ok) {
            console.error('Error checking email configuration: HTTP status', response.status);
            return;
        }
        
        const data = await response.json();
        
        const emailNotConfigured = document.getElementById('emailNotConfigured');
        if (emailNotConfigured) {
            if (!data.email_configured) {
                emailNotConfigured.classList.remove('hidden');
            } else {
                emailNotConfigured.classList.add('hidden');
            }
        }
        
        // Update stats if element exists
        const vesselCount = document.getElementById('vesselCount');
        const cargoCount = document.getElementById('cargoCount');
        const emailCount = document.getElementById('emailCount');
        
        if (vesselCount) vesselCount.textContent = data.vessel_count || 0;
        if (cargoCount) cargoCount.textContent = data.cargo_count || 0;
        if (emailCount) emailCount.textContent = data.processed_emails || 0;
        
        // Update last processed time
        const lastProcessed = document.getElementById('lastProcessed');
        if (lastProcessed) {
            if (data.last_processed) {
                const processedDate = new Date(data.last_processed);
                lastProcessed.textContent = `Last processed: ${processedDate.toLocaleString()}`;
            } else {
                lastProcessed.textContent = 'Last processed: Never';
            }
        }
    } catch (error) {
        console.error('Error checking email configuration:', error);
    }
}

// Helper functions
function formatDate(dateString) {
    if (!dateString) return '-';
    const date = new Date(dateString);
    return date.toLocaleDateString();
}

function formatQuantity(quantity) {
    if (!quantity) return '-';
    return quantity.toLocaleString() + ' MT';
}

// Load vessels function
async function loadVessels() {
    const vesselsTable = document.getElementById('vessels-table');
    if (!vesselsTable) return;
    
    try {
        // Show loading state
        vesselsTable.innerHTML = '<tr><td colspan="7" class="text-center py-4">Loading vessels...</td></tr>';
        
        const response = await fetch('/api/v1/vessels');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const vessels = await response.json();
        
        if (vessels.length === 0) {
            vesselsTable.innerHTML = '<tr><td colspan="7" class="text-center py-4">No vessels found</td></tr>';
            return;
        }
        
        // Render vessels
        vesselsTable.innerHTML = '';
        vessels.forEach(vessel => {
            const row = document.createElement('tr');
            row.className = 'hover:bg-gray-50';
            
            const status = getVesselStatus(vessel);
            
            row.innerHTML = `
                <td class="px-4 py-3 whitespace-nowrap">
                    <div class="text-sm font-medium text-gray-900">${vessel.name || '-'}</div>
                </td>
                <td class="px-4 py-3 whitespace-nowrap">
                    <div class="text-sm text-gray-900">${vessel.vessel_type || '-'}</div>
                </td>
                <td class="px-4 py-3 whitespace-nowrap">
                    <div class="text-sm text-gray-900">${vessel.position || '-'}</div>
                </td>
                <td class="px-4 py-3 whitespace-nowrap">
                    <div class="text-sm text-gray-900">${formatQuantity(vessel.dwt)}</div>
                </td>
                <td class="px-4 py-3 whitespace-nowrap">
                    ${status}
                </td>
                <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                    ${formatDate(vessel.created_at)}
                </td>
                <td class="px-4 py-3 whitespace-nowrap">
                    <button onclick="showAvailableCargoes(${vessel.id}, '${vessel.name}')" 
                            class="text-indigo-600 hover:text-indigo-900 font-medium">
                        Find Cargoes
                    </button>
                </td>
            `;
            vesselsTable.appendChild(row);
        });
        
        // Update last updated timestamp
        const lastUpdate = document.getElementById('last-update');
        if (lastUpdate) {
            lastUpdate.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
        }
    } catch (error) {
        console.error('Error loading vessels:', error);
        vesselsTable.innerHTML = '<tr><td colspan="7" class="text-center py-4 text-red-500">Error loading vessels: ' + error.message + '</td></tr>';
    }
}

// Vessel status helper
function getVesselStatus(vessel) {
    const now = new Date();
    if (vessel.eta) {
        const eta = new Date(vessel.eta);
        if (eta > now) {
            return `<span class="px-2 py-1 text-xs font-semibold rounded-full bg-yellow-100 text-yellow-800">
                        ETA: ${eta.toLocaleDateString()}
                    </span>`;
        }
    }
    if (vessel.open_date) {
        const openDate = new Date(vessel.open_date);
        if (openDate <= now) {
            return `<span class="px-2 py-1 text-xs font-semibold rounded-full bg-green-100 text-green-800">
                        Open
                    </span>`;
        }
        return `<span class="px-2 py-1 text-xs font-semibold rounded-full bg-blue-100 text-blue-800">
                    Opens: ${openDate.toLocaleDateString()}
                </span>`;
    }
    return `<span class="px-2 py-1 text-xs font-semibold rounded-full bg-gray-100 text-gray-800">
                No Status
            </span>`;
}

// Load cargoes function
async function loadCargoes() {
    const cargoesTable = document.getElementById('cargoes-table');
    if (!cargoesTable) return;
    
    try {
        // Show loading state
        cargoesTable.innerHTML = '<tr><td colspan="6" class="text-center py-4">Loading cargoes...</td></tr>';
        
        const response = await fetch('/api/v1/cargoes');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const cargoes = await response.json();
        
        if (cargoes.length === 0) {
            cargoesTable.innerHTML = '<tr><td colspan="6" class="text-center py-4">No cargoes found</td></tr>';
            return;
        }
        
        // Render cargoes
        cargoesTable.innerHTML = '';
        cargoes.forEach(cargo => {
            const row = document.createElement('tr');
            row.className = 'hover:bg-gray-50';
            row.innerHTML = `
                <td class="px-6 py-4">
                    <div class="text-sm font-medium text-gray-900">${cargo.cargo_type || '-'}</div>
                </td>
                <td class="px-6 py-4">
                    <div class="text-sm text-gray-900">${formatQuantity(cargo.quantity)}</div>
                </td>
                <td class="px-6 py-4">
                    <div class="text-sm text-gray-900">${cargo.load_port || '-'} → ${cargo.discharge_port || '-'}</div>
                </td>
                <td class="px-6 py-4">
                    <div class="text-sm text-gray-900">${cargo.rate || '-'}</div>
                </td>
                <td class="px-6 py-4">
                    <div class="text-sm text-gray-500">${formatDate(cargo.created_at)}</div>
                </td>
                <td class="px-6 py-4">
                    <button onclick="showAvailableVessels(${cargo.id})" 
                            class="text-indigo-600 hover:text-indigo-900 text-sm font-medium">
                        Find Vessels
                    </button>
                </td>
            `;
            cargoesTable.appendChild(row);
        });
        
        // Update last updated timestamp
        const lastUpdate = document.getElementById('last-update');
        if (lastUpdate) {
            lastUpdate.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
        }
    } catch (error) {
        console.error('Error loading cargoes:', error);
        cargoesTable.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-red-500">Error loading cargoes: ' + error.message + '</td></tr>';
    }
}

// Process emails function
async function processEmails() {
    const button = event.target;
    if (!button) return;
    
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = 'Processing...';
    
    try {
        const response = await fetch('/process-emails', { 
            method: 'POST',
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.status === 'success') {
            // Show success message
            const statusEl = document.createElement('div');
            statusEl.className = 'fixed top-4 right-4 bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded';
            statusEl.innerHTML = `
                <strong class="font-bold">Success!</strong>
                <span class="block sm:inline">${data.message}</span>
            `;
            document.body.appendChild(statusEl);
            
            // Remove message after 5 seconds
            setTimeout(() => {
                statusEl.remove();
            }, 5000);
            
            // Refresh data after processing
            setTimeout(() => {
                const vesselsTable = document.getElementById('vessels-table');
                if (vesselsTable) loadVessels();
                
                const cargoesTable = document.getElementById('cargoes-table');
                if (cargoesTable) loadCargoes();
                
                // Update email stats
                checkEmailConfiguration();
            }, 2000);
        } else {
            // Show error message
            alert(`Error: ${data.message}`);
        }
    } catch (error) {
        console.error('Error processing emails:', error);
        alert(`Error processing emails: ${error.message}`);
    } finally {
        // Reset button
        button.disabled = false;
        button.textContent = originalText;
    }
}