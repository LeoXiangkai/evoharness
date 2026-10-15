package example;
import javax.persistence.Entity;
import org.springframework.scheduling.annotation.Async;
import org.springframework.transaction.annotation.Transactional;
@Entity public class BadService {
  private String password = "abcdefgh1234";
  @Transactional public void write() { }
  @Async private void background() { }
  void swallow() { try { work(); } catch (Exception e) { // ignored
  } }
  void trace() { try { work(); } catch (Exception e) { e.printStackTrace(); } }
  void console() { System.out.println("bad"); }
  void work() { }
}
