import sys
import os
import serial
import serial.tools.list_ports
import threading
import csv
import re
from datetime import datetime
import time
from PyQt5 import QtSerialPort,QtWidgets
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QComboBox, QPushButton, QTextEdit,
    QVBoxLayout, QHBoxLayout, QCheckBox, QFileDialog, QTabWidget, QLineEdit,QDesktopWidget,
    QSizePolicy,QSpacerItem,QPlainTextEdit,QGridLayout)
from PyQt5.QtCore import QTimer,Qt, QIODevice,QObject,pyqtSignal
from PyQt5.QtGui import QDoubleValidator
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import queue

# Initialize global variables
serial_conn = None
data_running = False
serial_data_queue = queue.Queue()

class SerialReader(QObject):
    data_received = pyqtSignal(bytes)

    def __init__(self):
        super().__init__()
        self.serial_port = QtSerialPort.QSerialPort()

        self.serial_port.readyRead.connect(self.read_data)

    def open(self, port_name, baud_rate):
        # Open the serial port for read and write
        self.serial_port.setPortName(port_name)
        self.serial_port.setBaudRate(baud_rate)
        if not self.serial_port.open(QIODevice.ReadWrite):
            print("Failed to open serial port.")
            return False
        return True

    def close(self):
        # Close the serial port
        self.serial_port.close()

    def read_data(self):
        # Process incoming data as soon as it is received
        while self.serial_port.canReadLine():
            data = self.serial_port.readAll().data()
            self.data_received.emit(data)  # Emit the data to the main thread

    def write_data(self, data):
        # Send data to the serial port
        if self.serial_port.isOpen():
            self.serial_port.write(data.encode('utf-8'))

# Main GUI Class
class SerialMonitorPlotter(QMainWindow):
    def __init__(self):
        super().__init__()

        self.plotting = False
        self.recording = False
        self.recording_paused = False
        self.serial_data_queue = queue.Queue()
        self.serial_plot_queue = queue.Queue(300)
        self.time_queue = queue.Queue(300)
        self.save_samples = 0
        self.axes_max_value = 150000
        self.axes_min_value = 1000
        ## new method
        self.serial_reader = SerialReader()
        # self.serial_port.readyRead.connect(self.on_data_received)  # Connect the callback for data reception
        self.lastbyte = b''
        self.receivedLines = 0
        self.timeout = 100000
        self.avoid_first_data = 100
        self.setWindowTitle("Serial Monitor and Plotter")
        self.set_normalized_geometry(0.2, 0.2, 0.6, 0.6)
        
        # Create widgets and layouts
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        main_layout = QVBoxLayout(self.central_widget)

        # Serial Connection Widgets
        label1 = QLabel("Port :")
        label2 = QLabel("Baudrate:")
        label3 = QLabel("Terminator:")
        self.port_combo = QComboBox()
        self.port_combo.addItems([p.device for p in serial.tools.list_ports.comports()])
        available_baudrates= [300, 600, 1200, 2400, 4800, 9600,
            14400, 19200, 38400, 57600, 115200, 230400]
        self.baudrate_combo = QComboBox()
        self.baudrate_combo.addItems(map(str,available_baudrates))
        self.baudrate_combo.setCurrentText('9600')
        self.connect_button = QPushButton("Connect")

        self.terminator_combo = QComboBox()
        self.line_endings = {'LF':b'\n','CR':b'\r','CR/LF':b'\r\n'}
        for i,item in enumerate(self.line_endings):
            self.terminator_combo.addItem(item)
            self.terminator_combo.setItemData(i,self.line_endings[item])

        self.terminator_combo.setCurrentText('LF')

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        self.plot_button = QPushButton("plot")
        self.plot_button.setCheckable(True)
        self.plot_button.clicked.connect(self.toggle_plotting)
        self.clear_button = QPushButton("clear")
        self.clear_button.clicked.connect(self.clear_monitor)


        self.port_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)  # Stretch
        self.baudrate_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)  # Stretch
        self.terminator_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)  # Stretch
        # Layout for serial connection
        conn_layout = QHBoxLayout()
        conn_layout.addWidget(label1,alignment=Qt.AlignRight)
        conn_layout.addWidget(self.port_combo)
        conn_layout.addWidget(label2)
        conn_layout.addWidget(self.baudrate_combo)
        conn_layout.addWidget(label3)
        conn_layout.addWidget(self.terminator_combo)
        conn_layout.addItem(QSpacerItem(450,0,QSizePolicy.Expanding,QSizePolicy.Minimum))
        # conn_layout.addWidget(QSpacerItem())
        conn_layout.addWidget(self.connect_button)
        conn_layout.addWidget(self.plot_button)
        conn_layout.addWidget(self.clear_button)
        main_layout.addLayout(conn_layout)

        main_layout.addItem(QSpacerItem(0,50,QSizePolicy.Minimum,QSizePolicy.Expanding))

        # Tabs for Monitor and Plotter
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Monitor Tab
        self.monitor_text = QPlainTextEdit()
        self.monitor_text.setReadOnly(True)
        self.send_text = QLineEdit()
        self.send_button = QPushButton("Send")
        # self.send_button.clicked.connect(self.send_data)
        self.autoScroll = QCheckBox("Auto Scroll")
        self.autoScroll.setChecked(True)


        monitor_tab = QWidget()
        monitor_layout = QVBoxLayout(monitor_tab)
        
        monitor_layout.addWidget(self.monitor_text)
        send_layout = QHBoxLayout()
        send_layout.addWidget(self.send_text)
        send_layout.addWidget(self.send_button)
        send_layout.addWidget(self.autoScroll)
        
        monitor_layout.addLayout(send_layout)
        self.tabs.addTab(monitor_tab, "Monitor")

        # Plotter Tab
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)

        self.slider_lower = QtWidgets.QSlider(Qt.Vertical)
        self.slider_upper = QtWidgets.QSlider(Qt.Vertical)

        # Set the range for both sliders
        self.slider_lower.setRange(self.axes_min_value, self.axes_max_value)
        self.slider_upper.setRange(self.axes_min_value, self.axes_max_value)

        # Set initial positions
        self.slider_lower.setValue(self.axes_min_value)
        self.slider_upper.setValue(self.axes_max_value)

        # Connect sliders to update the range
        
        self.slider_upper.valueChanged.connect(self.update_axes_range)
        self.slider_lower.valueChanged.connect(self.update_axes_range)
        
        self.bl_val = QLineEdit()
        self.bl_val.setValidator(QDoubleValidator())
        self.set_bl = QPushButton("set baseline")
        self.set_bl.clicked.connect(self.set_baseline)
        

        plot_tab = QWidget()
        plot_layout = QVBoxLayout(plot_tab)
        axes_layout = QHBoxLayout()
        axes_layout.addWidget(self.canvas)
        axes_layout.addWidget(self.slider_lower)
        axes_layout.addWidget(self.slider_upper)
        plot_layout.addLayout(axes_layout)


        bl_layout = QHBoxLayout()
        
        
        bl_layout.addWidget(self.bl_val)
        bl_layout.addWidget(self.set_bl)
        bl_layout.addItem(QSpacerItem(900,0,QSizePolicy.Expanding,QSizePolicy.Minimum))
        plot_layout.addLayout(bl_layout)
        self.tabs.addTab(plot_tab, "Plotter")

        # Recording Data Widgets
        self.file_path_edit = QLineEdit()
        self.user_var1 = QLineEdit()
        self.user_var1.setText("0.0")
        self.user_var1.setValidator(QDoubleValidator())
        self.user_ch1 = QCheckBox("User variable1")
        self.user_var2 = QLineEdit()
        self.user_var2.setText("0.0")
        self.user_var2.setValidator(QDoubleValidator())
        self.user_ch2 = QCheckBox("User variable2")
        self.user_var3 = QLineEdit()
        self.user_var3.setText("0.0")
        self.user_var3.setValidator(QDoubleValidator())
        self.user_ch3 = QCheckBox("User variable3")
        self.record_button = QPushButton("Start Recording")
        self.record_button.clicked.connect(self.toggle_recording)
        self.pause_button = QPushButton("Pause Recording")
        self.pause_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.pause_button.clicked.connect(self.pause_recording)
        self.browse_button = QPushButton("Browse")
        self.browse_button.clicked.connect(self.browse_file)
        # Reference Value Field
        self.reference_value_field = QLineEdit()
        self.reference_value_field.setPlaceholderText("Reference Value")

        # Layout for recording
        record_layout = QGridLayout()
        record_layout.addWidget(self.user_var1,0,0)
        record_layout.addWidget(self.user_ch1,0,1)
        record_layout.addWidget(self.user_var2,1,0)
        record_layout.addWidget(self.user_ch2,1,1)
        record_layout.addWidget(self.user_var3,2,0)
        record_layout.addWidget(self.user_ch3,2,1)
        record_layout.addWidget(self.file_path_edit,0,5,1,3)
        record_layout.addWidget(self.browse_button,0,4)
        record_layout.addWidget(self.record_button,1,4)
        record_layout.addWidget(self.pause_button,1,5)

        record_layout.setColumnStretch(0,1)
        record_layout.setColumnStretch(5,2)
        record_layout.setColumnStretch(6,2)
        record_layout.addItem(QSpacerItem(200,0,QSizePolicy.Expanding,QSizePolicy.Minimum),0,3,3,1)
        
        main_layout.addItem(QSpacerItem(0,50,QSizePolicy.Minimum,QSizePolicy.Expanding))
        main_layout.addLayout(record_layout)

        # Timer to update the monitor and plot
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.plot_values = {}
        self.plots = {}
        self.time_labels = []

        self.timer_saver = QTimer()
        self.timer_saver.timeout.connect(self.save2csv)
    
    def set_normalized_geometry(self, x, y, width, height):
        screen = QDesktopWidget().screenGeometry()
        
        # Calculate actual pixel values
        x_pos = int(screen.width() * x)
        y_pos = int(screen.height() * y)
        w = int(screen.width() * width)
        h = int(screen.height() * height)

        # Set the geometry of the window
        self.setGeometry(x_pos, y_pos, w, h)



    def toggle_connection(self):
        # try:
            if not self.serial_reader.serial_port.isOpen():
                # Configure the serial port
                port_name = self.port_combo.currentText()
                baudrate = int(self.baudrate_combo.currentText())
                self.serial_reader.data_received.connect(self.Handle_data)
                self.serial_data_queue.maxsize = 1024
                
                while self.serial_data_queue.qsize() >0:
                    try:
                        self.serial_data_queue.get_nowait()
                    except:
                        pass
                # Open the serial port
                if self.serial_reader.open(port_name=port_name,baud_rate=baudrate):
                    self.connect_button.setText("Disconnect")
                    self.port_combo.setEnabled(False)
                    self.baudrate_combo.setEnabled(False)
                    self.terminator_combo.setEnabled(False)
                    self.start_time = float(time.time())
                    self.serial_data_queue = queue.Queue()
                    self.serial_plot_queue = queue.Queue(300)
                    self.time_queue = queue.Queue(300)
                    self.avoid_first_data = 100
                    self.variable_names = []
                    self.monitor_text.clear()
                else:
                    self.monitor_text_field.append("Failed to open port")
            else:
                # Close the serial port
                self.serial_reader.data_received.disconnect()
                self.serial_reader.close()
                self.connect_button.setText("Connect")
                self.port_combo.setEnabled(True)
                self.baudrate_combo.setEnabled(True)
                self.terminator_combo.setEnabled(True)
        # except Exception as e:
        #     self.monitor_text.appendPlainText(f"Error: {e}")

    def Handle_data(self,data): #old, working well for arduino which sends character one at each transmition
        # Read and decode the data
        if self.terminator_combo.currentData():
            data = data.split(self.terminator_combo.currentData())[:-1]
            
        for line in data:
            try:
                self.monitor_text.appendPlainText(line.decode(errors='ignore'))
            except Exception as e:
                self.monitor_text.append(f"Error: {e}")

            
        
        # while ( self.receivedLines >0):
        #     data = b''
        #     while self.terminator_combo.currentData() not in data:
        #         data += self.serial_data_queue.get_nowait()
        #     data = data.strip(self.terminator_combo.currentData()).decode(errors='ignore')
        #     self.monitor_text.appendPlainText(data)
        #     # print(self.monitor_text.depth())
        #     if self.autoScroll.isChecked():
        #         self.monitor_text.ensureCursorVisible()
        #     self.receivedLines -= 1
        #     try:
        #         if self.avoid_first_data > 0 :
        #            self.avoid_first_data -= 1
        #         else: 
        #             decoded_data = self.decode_vars(data)
        #             if self.serial_plot_queue.full():
        #                 self.serial_plot_queue.get_nowait()
        #                 self.time_queue.get_nowait()
        #             self.serial_plot_queue.put_nowait(decoded_data)
        #             self.time_queue.put_nowait(float(time.time())-self.start_time)
        #             self.save_samples += 1
                
        #     except Exception as e:
        #         self.monitor_text.append(f"Error: {e}")
                
        # self.serial_port.readyRead.connect(self.on_data_received)  # Connect the callback for data reception
        # # Update the plot if plotting is active
        # if self.plotting:
        #     self.update_plot(data)
    

    # def on_data_received(self): #new for usb virtual COM
    #     # Read and decode the data
    #     self.serial_port.readyRead.disconnect()
    #     tic = time.time().__float__()
    #     # print(self.terminator_combo.currentText())
    #     while time.time().__float__() < tic + 0.01:
    #         if self.serial_port.bytesAvailable()>0:
    #             a = self.serial_port.readAll().data()
    #             # print(a)
    #             # print(a.split(b'\n'))
    #             # print(f"Available bytes:{self.serial_port.bytesAvailable()}")
    #             # try:
    #             #     self.serial_data_queue.put_nowait(a)
    #             # except:
    #             #     pass
    #             tic = time.time().__float__()                
    #         else:
    #             time.sleep(0.005)
                
    #         # if self.terminator_combo.currentData() and self.terminator_combo.currentData()  in self.lastbyte+a:
    #         #     self.lastbyte = b''
    #         #     self.receivedLines += 1
    #         #     break
    #         # else:
    #         #     self.lastbyte = a[-1].to_bytes()


    #     else:
    #         pass
    #         # print("timeout")
    #         # print(self.serial_port.bytesAvailable())

            
        
    #     # while ( self.receivedLines >0):
    #     #     data = b''
    #     #     while self.terminator_combo.currentData() not in data:
    #     #         data += self.serial_data_queue.get_nowait()
    #     #     data = data.strip(self.terminator_combo.currentData()).decode(errors='ignore')
    #     #     self.monitor_text.appendPlainText(data)
    #     #     # print(self.monitor_text.depth())
    #     #     if self.autoScroll.isChecked():
    #     #         self.monitor_text.ensureCursorVisible()
    #     #     self.receivedLines -= 1
    #     #     try:
    #     #         if self.avoid_first_data > 0 :
    #     #            self.avoid_first_data -= 1
    #     #         else: 
    #     #             decoded_data = self.decode_vars(data)
    #     #             if self.serial_plot_queue.full():
    #     #                 self.serial_plot_queue.get_nowait()
    #     #                 self.time_queue.get_nowait()
    #     #             self.serial_plot_queue.put_nowait(decoded_data)
    #     #             self.time_queue.put_nowait(float(time.time())-self.start_time)
    #     #             self.save_samples += 1
                
    #     #     except Exception as e:
    #     #         self.monitor_text.append(f"Error: {e}")
                
    #     self.serial_port.readyRead.connect(self.on_data_received)  # Connect the callback for data reception
    #     # # Update the plot if plotting is active
    #     # if self.plotting:
    #     #     self.update_plot(data)


    def toggle_plotting(self):
        if self.plot_button.isChecked():
            self.timer.start(50)
        else:
            self.timer.stop()
        
    
    def clear_monitor(self):
        self.monitor_text.clear()

    def toggle_recording(self):
        if self.recording:
            self.recording=False
            self.record_button.setText("Start Recording")
            self.timer_saver.stop()
            self.csvfile.close()        
        else:
            self.recording=True
            self.record_button.setText("Stop Recording")
            self.timer_saver.start(100)
            self.csvfile = open(self.file_path_edit.text(),'a',newline='')

    def pause_recording(self):
        if self.recording_paused:
            self.recording_paused=False
            self.pause_button.setText("Pause Recording")
            self.timer_saver.start(100)      
        else:
            self.recording_paused= True
            self.pause_button.setText("Resume Recording")
            self.timer_saver.stop()


    def update_plot(self): #new
        
        if not self.serial_plot_queue.empty():
            # Extract data from the queue
            temp_data = list(self.serial_plot_queue.queue)  # Convert queue to list
            self.variable_names = temp_data[-1].keys()
            # Update plot data for each variable

            for var in self.variable_names:
                self.plot_values[var] = [entry[var] for entry in temp_data if var in entry.keys()]
            self.time_labels = list(self.time_queue.queue)
            # Clear the current plot and redraw
            # self.ax.clear()
            
            # Plot each variable as a line
            for var in self.variable_names:
                if var not in self.plots.keys():
                    self.plots[var], = self.ax.plot([],[], label=var)    
                
                self.plots[var].set_data((self.time_labels,self.plot_values[var]))
            
            self.ax.set_xlim(min(self.time_labels), max(self.time_labels))
            # Set labels and title
            self.ax.set_xlabel('time(s)')
            self.ax.set_ylabel('Value')
            self.ax.set_title('Real-time Data Plot')
            self.ax.grid('on')
            
            # Add legend
            self.ax.legend(loc='upper left')
            
            # Redraw the plot
            self.canvas.draw()

    
    def set_baseline(self):
        bl = float(self.bl_val.text())
        if 'bl' in self.plots.keys():
            self.plots['bl'].remove()
        self.plots['bl'] = self.ax.axhline(bl,linestyle = '--',label = 'Base line',color = 'r')
        self.canvas.draw()

    def decode_vars(self,data:str):
        pattern = r'(\w+):\s*([\d.]+)'
        tokens = re.findall(pattern, data)
        data_dict = {name: float(value) for name, value in tokens}
        return data_dict
        
    def update_axes_range(self):
        """Update the labels when sliders are adjusted."""
        lower_value = self.slider_lower.value()
        upper_value = self.slider_upper.value()
        
        # Ensure that the lower value is not greater than the upper value
        if lower_value > upper_value:
            self.slider_upper.setValue(lower_value)
        
        if upper_value < lower_value:
            self.slider_lower.setValue(upper_value)
        
        self.ax.set_ylim(lower_value,upper_value)

    def browse_file(self):
        file_path = QFileDialog.getExistingDirectory(self, "Save CSV", "")
        if file_path:
            if os.path.exists(file_path + "/recorded_data.csv"):
                self.file_path_edit.setText(file_path+"/recorded_data.csv")
                print("file exicts")
            else:
                with open(file_path+"/recorded_data.csv",'w') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow(['time']+list(self.variable_names) + ['userval1', 'userval2','userval3'])
                    

                self.file_path_edit.setText(file_path+"/recorded_data.csv")
                print("file created")
            


    def save2csv(self):
        if (not self.csvfile.closed):
            if(self.save_samples >= 100):
                writer = csv.writer(self.csvfile)
                rowlist = [self.time_labels[-100:]] + [self.plot_values[var][-100:] for var in self.variable_names]
                rowlist += [[float(self.user_var1.text()) for _ in range(100)]]
                rowlist += [[float(self.user_var2.text()) for _ in range(100)]]
                rowlist += [[float(self.user_var3.text()) for _ in range(100)]]
                writer.writerows(zip(*rowlist))
                self.save_samples -= 100



# Run Application
app = QApplication(sys.argv)
window = SerialMonitorPlotter()
window.show()
sys.exit(app.exec_())