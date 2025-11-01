import networkx as nx
import random
import time
import threading
import json
from queue import Queue, Empty

# --- Configuration ---
TIMEOUT = 0.5
HELLO_INTERVAL = 0.1
MESSAGE_TYPES = ["HELLO", "GET_NEIGHBORS", "SET_NEIGHBORS", "SET_TOPOLOGY", "DATA", "DISCONNECT"]

# --- Data Structures ---

class Message:
    def __init__(self, sender_id, receiver_id, msg_type, data=None):  
        self.sender_id = sender_id
        self.receiver_id = receiver_id
        self.msg_type = msg_type
        self.data = data if data is not None else {}
        self.timestamp = time.time()

class Link:
    def __init__(self, neighbor_id, cost, loss_prob): 
        self.neighbor_id = neighbor_id
        self.cost = cost
        self.loss_prob = loss_prob

class Connection:
    def __init__(self, loss_prob=0.0):  
        self.queue = Queue()
        self.loss_prob = loss_prob
        self.sent_count = 0

    def put(self, msg):
        self.sent_count += 1
        if random.random() > self.loss_prob:
            self.queue.put(msg)
            return True
        return False

    def get(self, timeout=None):
        try:
            return self.queue.get(timeout=timeout)
        except Empty:
            return None

# --- Router Base Class ---

class Router(threading.Thread):
    def __init__(self, router_id, links, dr_connection, all_connections): 
        super().__init__()  
        self.router_id = router_id
        self.links = links  # {neighbor_id: Link}
        self.dr_connection = dr_connection
        self.all_connections = all_connections  # dict[(src, dst)] = Connection
        self.topology_graph = nx.DiGraph()
        self.shortest_paths = {}
        self.neighbors = list(links.keys())
        self.running = True
        self.log = []
        self.lock = threading.Lock()
        self.daemon = True

    def run(self):
        self.log.append(f"Router {self.router_id} started. Neighbors: {self.neighbors}")
        hello_thread = threading.Thread(target=self._hello_sender, daemon=True)
        hello_thread.start()

        while self.running:
            msg = self.dr_connection.get(timeout=0.01)
            if msg:
                self._handle_message(msg)

            for neighbor_id in list(self.neighbors):
                conn = self.all_connections.get((neighbor_id, self.router_id))
                if conn:
                    msg = conn.get(timeout=0.001)
                    if msg:
                        self._handle_message(msg)

            time.sleep(0.001)

    def stop(self):
        self.running = False

    def _hello_sender(self):
        while self.running:
            for neighbor_id in self.neighbors:
                conn = self.all_connections.get((self.router_id, neighbor_id))
                if conn:
                    hello_msg = Message(self.router_id, neighbor_id, "HELLO", {
                        "timestamp": time.time(),
                        "neighbor_info": self.router_id
                    })
                    conn.put(hello_msg)
            time.sleep(HELLO_INTERVAL)

    def _handle_message(self, msg):
        with self.lock:
            if msg.msg_type == "SET_TOPOLOGY":
                self.log.append("received topology")
                self.topology_graph = msg.data["graph"]
                self._calculate_shortest_paths()

            elif msg.msg_type == "DATA":
                path_log = msg.data.get('path', [])
                if path_log:
                    self.log.append(f"received message from {path_log[-1]}: {path_log}")
                else:
                    self.log.append(f"received message from {msg.sender_id}")

                if msg.receiver_id == self.router_id:
                    self.log.append(f"DATA delivered to {self.router_id}")
                else:
                    self._forward_data(msg)

            elif msg.msg_type == "HELLO":
                # You could process HELLO here if desired
                pass

    def _calculate_shortest_paths(self):
        try:
            self.shortest_paths = {}
            for target in self.topology_graph.nodes():
                if target != self.router_id:
                    try:
                        path = nx.shortest_path(
                            self.topology_graph,
                            source=self.router_id,
                            target=target,
                            weight='cost'
                        )
                        self.shortest_paths[target] = path
                    except nx.NetworkXNoPath:
                        self.shortest_paths[target] = []

            path_str = "new shortest ways: {"
            for target, path in self.shortest_paths.items():
                path_str += f"\n  {target}: {path},"
            path_str += "\n}"
            self.log.append(path_str)
        except Exception as e:
            self.log.append(f"Error calculating paths: {e}")

    def _forward_data(self, msg):
        import copy
        target_id = msg.receiver_id
        
        # إضافة فحص لمنع التكرار - إذا كان الموجه موجود بالفعل في المسار
        current_path = msg.data.get('path', [])
        if self.router_id in current_path:
            self.log.append(f"Loop detected: router {self.router_id} already in path {current_path}")
            return
            
        if target_id in self.shortest_paths and len(self.shortest_paths[target_id]) > 1:
            full_path = self.shortest_paths[target_id]
            try:
                current_index = full_path.index(self.router_id)
                next_hop = full_path[current_index + 1]
                new_msg = Message(msg.sender_id, msg.receiver_id, msg.msg_type, copy.deepcopy(msg.data))
                new_msg.data['path'] = new_msg.data.get('path', []) + [self.router_id]

                start = new_msg.data['path'][0] if new_msg.data['path'] else msg.sender_id
                self.log.append(f"transferred message from {start} to {target_id}: {new_msg.data['path']}")

                conn = self.all_connections.get((self.router_id, next_hop))
                if conn:
                    conn.put(new_msg)
                else:
                    self.log.append(f"Error: No connection to next hop {next_hop}")
            except (ValueError, IndexError) as e:
                self.log.append(f"Error in path processing: {e}")
        else:
            self.log.append(f"cannot send message to {target_id}")

    def send_data(self, target_id, data_content="Test data"):
        with self.lock:
            if target_id == self.router_id:
                self.log.append("Cannot send to self")
                return
                
            if target_id in self.shortest_paths and len(self.shortest_paths[target_id]) > 1:
                full_path = self.shortest_paths[target_id]
                next_hop = full_path[1]

                initial_data = {"content": data_content, "path": [self.router_id]}
                data_msg = Message(self.router_id, target_id, "DATA", initial_data)

                self.log.append(f"sent message to {target_id}: {[self.router_id]}")
                conn = self.all_connections.get((self.router_id, next_hop))
                if conn:
                    conn.put(data_msg)
                else:
                    self.log.append(f"Error: Cannot send, no connection to {next_hop}")
            else:
                self.log.append(f"cannot send message to {target_id}")

# --- Designated Router Class ---

class DesignatedRouter(threading.Thread):
    def __init__(self, router_ids, topology_data, router_connections): 
        super().__init__()  
        self.router_ids = router_ids
        self.router_connections = router_connections
        self.topology_data = topology_data
        self.topology_graph = nx.DiGraph()
        self.running = True
        self.log = []
        self.lock = threading.Lock()
        self.daemon = True

    def run(self):
        self.log.append("Designated Router started.")
        self._build_topology()
        self._distribute_topology()
        while self.running:
            time.sleep(0.1)

    def stop(self):
        self.running = False

    def _build_topology(self):
        G = nx.DiGraph()
        for router_id in self.router_ids:
            G.add_node(router_id)
        for router_id, links in self.topology_data.items():
            for neighbor_id, link in links.items():
                G.add_edge(router_id, neighbor_id, cost=link.cost)
                G.add_edge(neighbor_id, router_id, cost=link.cost)
        self.topology_graph = G
        self.log.append("Built topology graph")

    def _distribute_topology(self):
        with self.lock:
            if self.topology_graph.number_of_nodes() > 0:
                self.log.append("Distributing topology to all routers.")
                for router_id, conn in self.router_connections.items():
                    msg = Message("DR", router_id, "SET_TOPOLOGY", {
                        "graph": self.topology_graph.copy()
                    })
                    conn.put(msg)
            else:
                self.log.append("Topology not built yet.")

# --- Simulation Setup ---

def setup_simulation(topology_data):
    router_ids = list(topology_data.keys())
    all_connections = {}
    for src in router_ids:
        for dst, link in topology_data[src].items():
            all_connections[(src, dst)] = Connection(link.loss_prob)

    dr_connections = {r: Connection(0.0) for r in router_ids}

    routers = {}
    for r in router_ids:
        routers[r] = Router(r, topology_data[r], dr_connections[r], all_connections)

    dr = DesignatedRouter(router_ids, topology_data, dr_connections)
    return routers, dr, all_connections

def run_ospf_simulation(topology_data, sender_id, receiver_id, run_time=3):
    print(f"\n=== Running OSPF Simulation: Router {sender_id} -> Router {receiver_id} ===")
    routers, dr, all_connections = setup_simulation(topology_data)
    dr.start()
    for r in routers.values():
        r.start()

    time.sleep(1)
    
    if sender_id in routers:
        routers[sender_id].send_data(receiver_id)
    
    time.sleep(run_time)

    for r in routers.values():
        r.stop()
    dr.stop()

    for r in routers.values():
        r.join(timeout=1)
    dr.join(timeout=1)
    return routers, dr

# --- Topology Definitions ---

def get_linear_topology():
    return {
        0: {1: Link(1, 1.0, 0.0)},
        1: {0: Link(0, 1.0, 0.0), 2: Link(2, 1.0, 0.0)},
        2: {1: Link(1, 1.0, 0.0), 3: Link(3, 1.0, 0.0)},
        3: {2: Link(2, 1.0, 0.0), 4: Link(4, 1.0, 0.0)},
        4: {3: Link(3, 1.0, 0.0)}
    }

def get_ring_topology():
    return {
        0: {1: Link(1, 1.0, 0.0), 4: Link(4, 1.0, 0.0)},
        1: {0: Link(0, 1.0, 0.0), 2: Link(2, 1.0, 0.0)},
        2: {1: Link(1, 1.0, 0.0), 3: Link(3, 1.0, 0.0)},
        3: {2: Link(2, 1.0, 0.0), 4: Link(4, 1.0, 0.0)},
        4: {3: Link(3, 1.0, 0.0), 0: Link(0, 1.0, 0.0)}
    }

def get_star_topology():
    return {
        0: {1: Link(1, 1.0, 0.0), 2: Link(2, 1.0, 0.0), 3: Link(3, 1.0, 0.0), 4: Link(4, 1.0, 0.0)},
        1: {0: Link(0, 1.0, 0.0)},
        2: {0: Link(0, 1.0, 0.0)},
        3: {0: Link(0, 1.0, 0.0)},
        4: {0: Link(0, 1.0, 0.0)}
    }

def simulate_all_topologies():
    print("OSPF Protocol Simulation - Multiple Topologies")
    print("=" * 50)
    all_logs = {}

    print("\n1. Linear Topology: Router 0 -> Router 4")
    routers_linear, _ = run_ospf_simulation(get_linear_topology(), 0, 4)
    all_logs["linear"] = {f"router_{i}": routers_linear[i].log for i in routers_linear}

    print("\n2. Ring Topology: Router 0 -> Router 2")
    routers_ring, _ = run_ospf_simulation(get_ring_topology(), 0, 2)
    all_logs["ring"] = {f"router_{i}": routers_ring[i].log for i in routers_ring}

    print("\n3. Star Topology: Router 4 -> Router 3")
    routers_star, _ = run_ospf_simulation(get_star_topology(), 4, 3)
    all_logs["star"] = {f"router_{i}": routers_star[i].log for i in routers_star}

    with open("ospf_simulation_logs.json", "w") as f:
        json.dump(all_logs, f, indent=2)

    print("\nSimulation completed. Logs saved to ospf_simulation_logs.json")

    print("\n=== Simulation Summary ===")
    for topology, logs in all_logs.items():
        print(f"\n{topology.upper()} TOPOLOGY:")
        for router, router_logs in logs.items():
            print(f"  {router}: {len(router_logs)} log entries")
            for log_entry in router_logs[-3:]:
                print(f"    - {log_entry}")

    return all_logs

if __name__ == "__main__":  
    random.seed(42)
    logs = simulate_all_topologies()
