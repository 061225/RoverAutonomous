#!/usr/bin/env python3

from UDPComms import Publisher, Subscriber, timeout
import math
import time
import random

import enum

from typing import Dict, Tuple, Sequence, List

class Situation(enum.Enum):
    ok = enum.auto()
    stuck = enum.auto()
    done = enum.auto()

class StateMachine:
    def __init__(self, parent, rover):
        self.parent = parent
        self.rover = rover

    def run(self) -> Situation:
        raise NotImplementedError 

class TaskManager:
    def __init__(self):
        self.rover = Rover()

        self.post  = PostTask(self, self.rover)
        # self.gate  = GateTask(self, self.rover)
        # self.retu  = PathFollower(self, self.rover)
        self.follow  = FollowTask(self, self.rover)

    def run(self):
        print(type(self).__name__, "-->", end = " ")

        cmd = self.rover.get_cmd()
        print( self.rover.get_pose() )

        if cmd['command'] == "auto":

            self.rover.logger.add(self.rover.get_pose() , "red")
            if cmd['end_mode'] == "tennis":
                self.post.run()
                return
            
            if cmd['end_mode'] == "none":
                self.follow.run()
                return
        else:
            print("Not moving")
        
        # if cmd['task'] == "post":
        #     self.post.run()
        #     return
        
        # if cmd['task'] == "gate":
        #     self.gate.run()
        #     return

    def begin(self):
        while 1:
            start_time = time.monotonic()
            self.rover.next_iteration()
            self.run()

            while time.monotonic() - start_time < 0.1:
                pass

class FollowTask(StateMachine):
    def __init__(self,*args):
        super().__init__(*args)
        self.pathfollower  = PathFollower(self, self.rover)
        self.final_waypoint_tol = 2

    def run(self) -> Situation:
        print(type(self).__name__, "-->", end = " ")
        waypoints = self.rover.get_waypoints()

        if self.rover.get_pose().dist( waypoints[-1] ) < self.final_waypoint_tol:
            print("DONE!")
        else:
            self.pathfollower.run(waypoints)


class PostTask(StateMachine):
    class State(enum.Enum):
        Following = enum.auto()
        Searching = enum.auto()
        Final     = enum.auto()

    def __init__(self,*args):
        super().__init__(*args)

        self.pathfollower  = PathFollower(self, self.rover)
        self.search        = Search(self, self.rover)
        self.finalapproach = FinalApproachPost(self, self.rover)

        self.final_waypoint_tol = 7
        self.aruco_visual_tolerance = 1

        self.state = self.State.Following

    def run(self) -> Situation:
        print(type(self).__name__, "-->", end = " ")
        waypoints = self.rover.get_waypoints()

        if self.state == self.State.Following:
            self.pathfollower.run(waypoints)
            if self.rover.get_pose().dist( waypoints[-1] ) < self.final_waypoint_tol:
                print("reached final waypoint, entering search")
                self.state = self.State.Searching
            if len(self.rover.get_aruco()) != 0:
                print("saw something entering search")
                self.state = self.State.Searching
            return

        elif self.state == self.State.Searching:
            searched = [4,3] # TODO HARDCODED
            self.search.run(waypoints[-1], searched )
            markers = self.rover.get_aruco()
            if min( [markers[i].dist for i in searched if i in markers.keys()] + [float("inf")] ) < self.aruco_visual_tolerance:
                self.state = self.State.Final

        elif self.state == self.State.Final:
            self.rover.lights.send("done")
            print("DONE!!!")
            # self.finalapproach.run()

class PathFollower(StateMachine):
    def __init__(self,*args):
        super().__init__(*args)
        self.lookahead_radius = 6
        self.start_point = None

    def run(self, waypoints: List['Pose'] , slow = False) -> Situation:
        print(type(self).__name__, "-->", end = " ")
        if self.start_point is None:
            self.save_start_point()

        point = self.find_lookahead(waypoints)
        angle = self.get_angle(point)
        self.send_velocities(angle)

    def save_start_point(self):
        self.start_point = self.rover.get_pose()

    def find_lookahead(self,waypoints: List['Pose']) -> 'Pose':
        start_waypoints = [self.start_point] + waypoints
        waypoint_pairs = zip(start_waypoints[::-1][1:], start_waypoints[::-1][:-1])

        for p1, p2 in waypoint_pairs:
            lookahead = self.lookahead_line(p1,p2)
            if lookahead is not None:
                return lookahead
        else:
            print("NO intersection found!")

        distances = []
        for p1, p2 in waypoint_pairs:
            lookahead = self.lookahead_line(p1,p2,project = True)
            if lookahead is not None:
                distances.append( (self.rover.get_pose().dist(lookahead), lookahead) )

        for p1 in waypoints:
            distances.append( (self.rover.get_pose().dist(p1), p1) )

        return min(distances)[1]

    def lookahead_line(self, start: 'Pose', end: 'Pose', project=False) -> 'Pose':
        robot = self.rover.get_pose()

        if robot.dist(end) < self.lookahead_radius:
            return end

        a = (end.x - start.x)**2 + (end.y - start.y)**2
        b = 2*( (end.x-start.x)*(start.x-robot.x) + (end.y-start.y)*(start.y-robot.y))
        c = (start.x-robot.x)**2 + (start.y-robot.y)**2 - self.lookahead_radius**2

        if b**2 - 4*a*c <=0:
            if project:
                t = -b/(2*a)
            else:
                return None
        else:
            t = (-b + math.sqrt(b**2 - 4*a*c))/(2*a)

        if not t<=1:
            return None
        if not t>=0:
            return None

        x_look = t * end.x + (1-t) * start.x
        y_look = t * end.y + (1-t) * start.y

        return Pose(x_look, y_look)


    def get_angle(self, lookahead: 'Pose'):
        pose = self.rover.get_pose()
        target = pose.bearing(lookahead)

        # anti clockwise positive
        return ((target - pose.a + math.pi)%(2*math.pi) - math.pi)


    def send_velocities(self, angle):
        turn_rate = -200*angle/math.pi

        if math.fabs(angle) < math.radians(10):
            forward_rate = 140
        elif math.fabs(angle) < math.radians(60):
            forward_rate = 100
        elif math.fabs(angle) < math.radians(140):
            forward_rate = 30
        else:
            forward_rate = 0

        self.rover.send_vel(forward_rate, turn_rate)


class SlowPathFollower(PathFollower):
    def send_velocities(self, angle):
        turn_rate = -130*angle/math.pi

        if math.fabs(angle) < math.radians(10):
            forward_rate = 90
        elif math.fabs(angle) < math.radians(60):
            forward_rate = 50
        elif math.fabs(angle) < math.radians(140):
            forward_rate = 0
        else:
            forward_rate = 0

        self.rover.send_vel(forward_rate, turn_rate)

class FinalApproachGate(StateMachine):
    pass

class FinalApproachPost(StateMachine):
    def __init__(self,*args):
        super().__init__(*args)
        self.stoping_distance = 1

    def run(self) -> Situation:
        print(type(self).__name__, "-->", end = " ")
        #TODO WRITE THIS


class Unstuck(StateMachine):
    pass

class Search(StateMachine):
    def __init__(self,*args):
        super().__init__(*args)
        self.search_radius = 10
        self.accept_radius = 1
        self.guess_timeout = 30

        self.seen = False

        self.pathfollower = SlowPathFollower(self,self.rover)
        self.guess = None
        self.last_guess_time = float('-inf')

    def run(self, endpoint: 'Pose', searched) -> Situation:
        print(type(self).__name__, "-->", end = " ")
        markers = self.rover.get_aruco()

        if set(markers.keys()).intersection(set(searched)):
            for idx, marker in markers.items():
                if idx in searched:
                    g = self.rover.get_pose().extraploate(marker)
                    self.rover.logger.add(g, "green")
                    print("New Guess from observation")
                    self.set_guess(g)
                    self.seen = True
                    self.pathfollower.send_velocities( -marker.a )
                    return
                    break

        elif self.guess is None:
            print("New random guess from initial")
            self.rand_guess(endpoint)

        elif (self.rover.get_pose().dist(self.guess) < self.accept_radius) and not self.seen:
            #TODO: this can overwrite 
            print("New random guess from reaching previous")
            self.rand_guess(endpoint)

        elif time.monotonic() - self.last_guess_time > self.guess_timeout:
            print("New random guess from timeout")
            self.rand_guess(endpoint)

        self.pathfollower.run([endpoint])

    def rand_guess(self,endpoint: 'Pose'):
        rand_x = (2*random.random()-1)*self.search_radius
        rand_y = (2*random.random()-1)*self.search_radius
        print(rand_x, rand_y)
        self.set_guess(Pose(endpoint.x + rand_x, endpoint.y + rand_y))

    def set_guess(self, guess: 'Pose'):
        self.guess = guess
        self.last_guess_time = time.monotonic()
        self.pathfollower.save_start_point()

class Logger:
    def __init__(self, port):
        self.pub = Publisher(port)
        self.points = []

    def next(self):
        self.pub.send(self.points)
        self.points = []

    def add(self, pose, color):
        self.points.append( [(pose.x, pose.y), pose.a, color] )

class Pose:
    def __init__(self, x, y, angle=None):
        self.x = x
        self.y = y
        self.a = angle

    def dist(self, other: 'Pose'):
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)

    def bearing(self,other: 'Pose'):
        return math.atan2( other.x - self.x, other.y - self.y)
     
    def extraploate(self, bearing: 'Bearing') -> 'Pose':
        distance = bearing.dist
        angle = bearing.a
        x = self.x + distance * math.cos(self.a - angle)
        y = self.y + distance * math.sin(self.a - angle)
        return Pose(x,y)

    def __repr__(self):
        return "Pose(x="+str(self.x)+", y="+str(self.y)+", a="+str(self.a)+")"

class Bearing:
    def __init__(self, dist, angle):
        self.dist = dist
        self.a = angle

class Rover:
    def __init__(self):
        self.imu = Subscriber(8220, timeout=2)
        self.gps = Subscriber(8280, timeout=3)
        self.auto_control = Subscriber(8310, timeout=5)
        self.lights = Publisher(8311)

        self.cmd_vel = Publisher(8830)

        self.logger = Logger(8312)

        self.aruco = Subscriber(8390, timeout=3)

        time.sleep(2) # delay to give extra time for gps message
        self.start_gps = self.gps.recv()

        self.cmd_sent = False

    def project(self, lat, lon):
        lat_orig = self.start_gps['lat']
        lon_orig = self.start_gps['lon']
        RADIUS = 6371 * 1000
        lon_per_deg = RADIUS * 2 * math.pi / 360
        lat_per_deg = lon_per_deg * math.cos(math.radians(lat_orig))

        x = (lon - lon_orig) * lon_per_deg
        y = (lat - lat_orig) * lat_per_deg
        return (x,y)

    def get_pose(self):
        gps = self.gps.get()
        # heading = math.radians( 360 -self.imu.get()['angle'][0] )
        heading = math.radians( self.imu.get()['angle'][0] )
        return Pose( *self.project( gps['lat'], gps['lon']), heading)

    def send_vel(self, forward, twist):
        msg = {"f":forward, "t":twist} 
        if self.cmd_sent:
            print("ERROR Multiple commands being sent!")
        self.cmd_vel.send( msg )
        self.cmd_sent = True
        print(msg)

    def get_aruco(self):
        markers = self.aruco.get()
        out = {}
        for marker in markers:
            idx, dist, angle = marker['id'], marker['dist'], marker['angle']
            out[idx] = Bearing(dist, math.radians(angle))
        return out

    def get_cmd(self):
        return self.auto_control.get()

    def get_waypoints(self) -> List["Pose"]:
        wps = self.auto_control.get()['waypoints']
        out = []
        for waypoint in wps:
            out.append( Pose( *self.project( waypoint['lat'], waypoint['lon']) ) )
        return out
    
    def next_iteration(self):
        self.cmd_sent = False
        self.logger.next()


if __name__ == '__main__':
    a = TaskManager()
    a.begin()

